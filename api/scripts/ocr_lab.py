"""Reproducible, loss-aware OCR experiment for saved job-posting images.

Requires Pillow; OCR runners additionally require Tesseract or PaddleOCR.
The collection and run stages never infer relevance or discard repeated text.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import importlib.metadata
import json
import math
import functools
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_IMAGE_BYTES = 30 * 1024 * 1024
PRESETS = {
    "full": (0, 0),
    "small_no_overlap": (600, 0),
    "small_overlap": (600, 150),
    "current": (900, 150),
    "model_native": (2276, 150),
    "api_limit": (7700, 150),
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def image_info(data: bytes) -> tuple[str, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        return im.format or "unknown", im.width, im.height


def read_source(source: dict[str, str]) -> bytes:
    if "path" in source:
        data = Path(source["path"]).read_bytes()
    elif "url" in source:
        url = source["url"]
        if not url.startswith("https://"):
            raise ValueError("Image URLs must use HTTPS")
        req = urllib.request.Request(url, headers={"User-Agent": "jobboard-ocr-lab/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            if not response.url.startswith("https://"):
                raise ValueError("Redirected to a non-HTTPS URL")
            data = response.read(MAX_IMAGE_BYTES + 1)
    else:
        raise ValueError("Source needs a path or URL")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 30 MiB")
    image_info(data)
    return data


def collect(manifest_path: Path, root: Path) -> None:
    manifest = load(manifest_path)
    if not isinstance(manifest, list):
        raise ValueError("Manifest must be a JSON list")
    rows = []
    seen = set()
    for item in manifest:
        job_id = str(item["id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            raise ValueError(f"Unsafe id: {job_id!r}")
        if job_id in seen:
            raise ValueError(f"Duplicate id: {job_id}")
        seen.add(job_id)
        sources = item.get("images", [])
        if not sources:
            raise ValueError(f"No images for {job_id}")
        saved = []
        for index, source in enumerate(sources, 1):
            data = read_source(source)
            fmt, width, height = image_info(data)
            digest = sha256(data)
            legacy_path = root / "images" / f"{digest}.bin"
            extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif",
                         "BMP": "bmp", "TIFF": "tif"}.get(fmt, "bin")
            path = legacy_path if legacy_path.exists() else root / "images" / f"{digest}.{extension}"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and sha256(path.read_bytes()) != digest:
                raise RuntimeError(f"Hash collision at {path}")
            if not path.exists():
                path.write_bytes(data)
            saved.append({"index": index, "sha256": digest, "file": str(path.relative_to(root)),
                          "format": fmt, "width": width, "height": height, "bytes": len(data),
                          "source": source})
        rows.append({"id": job_id, "posting_url": item.get("posting_url", ""),
                     "query": item.get("query", ""), "rank": item.get("rank"),
                     "collected_at_utc": datetime.now(timezone.utc).isoformat(), "images": saved})
    collection_file = root / "collection.json"
    if collection_file.exists():
        old = load(collection_file)["jobs"]
        old_by_id = {j["id"]: j for j in old}
        for row in rows:
            prior = old_by_id.get(row["id"])
            if prior and [x["sha256"] for x in prior["images"]] != [x["sha256"] for x in row["images"]]:
                raise RuntimeError(f"Image changed for {row['id']}; use a new collection root or ID")
        rows = old + [row for row in rows if row["id"] not in old_by_id]
    dump(collection_file, {"schema": 1, "jobs": rows})
    print(f"Saved {sum(len(r['images']) for r in rows)} original images for {len(rows)} jobs")


def export_db_manifest(root: Path, limit: int) -> None:
    """Run inside the jobboard API container; read the current DB without changing it."""
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import Job

    manifest = []
    with SessionLocal() as db:
        for job in db.execute(select(Job).order_by(Job.id)).scalars():
            urls = (job.raw or {}).get("이미지 주소") or []
            if isinstance(urls, str):
                urls = [urls]
            if not urls:
                continue
            manifest.append({"id": str(job.id), "posting_url": job.source_key or "",
                             "query": "DB image backlog", "images": [{"url": u} for u in urls]})
            if limit and len(manifest) >= limit:
                break
    dump(root / "db_manifest.json", manifest)
    print(f"Exported {len(manifest)} jobs to {root / 'db_manifest.json'}")


def export_candidate_manifest(root: Path, limit: int) -> None:
    """품질 검증으로 DB 공고 저장은 보류한 이미지의 주소를 PC OCR 실험용으로 내보낸다."""
    from app.db import SessionLocal
    from app.models import SharedData

    with SessionLocal() as db:
        row = db.get(SharedData, "ocrCandidates")
        items = list(((row.value or {}).get("items") or {}).values()) if row else []
    items.sort(key=lambda x: x.get("found_on", ""), reverse=True)
    if limit:
        items = items[:limit]
    manifest = [{"id": item["id"], "posting_url": item["posting_url"], "query": "JobKorea OCR candidate",
                 "images": [{"url": url} for url in item["images"]]} for item in items if item.get("images")]
    dump(root / "candidate_manifest.json", manifest)
    print(f"Exported {len(manifest)} image candidates to {root / 'candidate_manifest.json'}")


def list_candidates(root: Path) -> None:
    jobs = load(root / "collection.json")["jobs"]
    candidates = []
    for job in jobs:
        images = job["images"]
        candidates.append({
            "id": job["id"], "posting_url": job["posting_url"], "image_count": len(images),
            "max_height": max(image["height"] for image in images),
            "max_aspect_ratio": round(max(image["height"] / image["width"] for image in images), 2),
            "images": [{key: image[key] for key in ("index", "file", "source", "width", "height")}
                       for image in images],
        })
    dump(root / "candidates.json", candidates)
    print(f"Wrote {len(candidates)} candidates to {root / 'candidates.json'}")


def select_sample(root: Path, tune: list[str], holdout: list[str]) -> None:
    jobs = {job["id"]: job for job in load(root / "collection.json")["jobs"]}
    ids = tune + holdout
    if not tune or not holdout:
        raise ValueError("Both tune and holdout need at least one job")
    if len(set(ids)) != len(ids):
        raise ValueError("A job may appear only once across tune and holdout")
    unknown = set(ids) - jobs.keys()
    if unknown:
        raise ValueError(f"Unknown job IDs: {', '.join(sorted(unknown))}")
    tune_hashes = {image["sha256"] for job_id in tune for image in jobs[job_id]["images"]}
    holdout_hashes = {image["sha256"] for job_id in holdout for image in jobs[job_id]["images"]}
    if tune_hashes & holdout_hashes:
        raise ValueError("The same original image appears in tune and holdout; group those jobs together")
    selection = {"tune": tune, "holdout": holdout}
    path = root / "sample.json"
    if path.exists() and load(path) != selection:
        raise ValueError("Sample is already fixed; use a separate experiment root to change it")
    dump(path, selection)
    print(f"Fixed sample: {len(tune)} tuning, {len(holdout)} holdout")


def sample_ids(root: Path, split: str | None) -> set[str] | None:
    path = root / "sample.json"
    if path.exists():
        if split is None:
            raise ValueError("Choose --split tune or --split holdout for the fixed sample")
        return set(load(path)[split])
    if split is not None:
        raise ValueError("Create a fixed sample with select before using --split")
    return None


def tiles(width: int, height: int, crop_height: int, overlap: int) -> list[tuple[int, int]]:
    if crop_height <= 0 or height <= crop_height:
        return [(0, height)]
    count = math.ceil(height / crop_height)
    step = math.ceil(height / count)
    return [(max(0, i * step - (overlap if i else 0)),
             min(height, (i + 1) * step + (overlap if i < count - 1 else 0)))
            for i in range(count)]


def ocr_tesseract(path: Path, psm: int, lang: str) -> str:
    if not shutil.which("tesseract"):
        raise RuntimeError("Install Tesseract with Korean and English language data")
    proc = subprocess.run(["tesseract", str(path), "stdout", "-l", lang, "--psm", str(psm)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    if proc.returncode:
        raise RuntimeError(proc.stderr[:500])
    return proc.stdout


@functools.lru_cache(maxsize=1)
def paddle_engine():
    # PaddleOCR is optional because its model dependencies are much larger.
    from paddleocr import PaddleOCR

    return PaddleOCR(lang="korean", ocr_version="PP-OCRv5", use_doc_orientation_classify=False,
                     use_doc_unwarping=False, use_textline_orientation=False)


def ocr_paddle(path: Path) -> str:
    engine = paddle_engine()
    pages = engine.predict(input=str(path))
    lines = []
    for page in pages:
        result = page.json
        result = result.get("res", result)
        lines.extend(str(x) for x in result.get("rec_texts", []))
    return "\n".join(lines)


def merge_exact_overlap(texts: list[str]) -> str:
    """Remove only identical boundary lines; retain nonidentical lines for review."""
    lines: list[str] = []
    for text in texts:
        incoming = text.splitlines()
        match = 0
        for size in range(1, min(len(lines), len(incoming), 30) + 1):
            if lines[-size:] == incoming[:size]:
                match = size
        lines.extend(incoming[match:])
    return "\n".join(lines).strip() + "\n"


def run(root: Path, engines: list[str], presets: list[str], tesseract_psms: list[int], lang: str,
        split: str | None = None) -> None:
    selected = sample_ids(root, split)
    from PIL import Image

    if "tesseract" in engines and not shutil.which("tesseract"):
        raise RuntimeError("Tesseract executable is unavailable")
    if "paddle" in engines:
        try:
            importlib.metadata.version("paddleocr")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError("PaddleOCR is unavailable") from exc
    versions = {"python": sys.version.split()[0], "pillow": importlib.metadata.version("Pillow")}
    if "tesseract" in engines:
        versions["tesseract"] = subprocess.run(["tesseract", "--version"], capture_output=True,
                                                text=True, encoding="utf-8").stdout.splitlines()[0]
    if "paddle" in engines:
        versions["paddleocr"] = importlib.metadata.version("paddleocr")
    collection = load(root / "collection.json")
    for job in collection["jobs"]:
        if selected is not None and job["id"] not in selected:
            continue
        for image in job["images"]:
            original = root / image["file"]
            data = original.read_bytes()
            if sha256(data) != image["sha256"]:
                raise RuntimeError(f"Original image changed: {original}")
            with Image.open(io.BytesIO(data)) as opened:
                source = opened.convert("RGB")
            for preset in presets:
                crop_height, overlap = PRESETS[preset]
                parts = tiles(source.width, source.height, crop_height, overlap)
                for engine in engines:
                    psms = tesseract_psms if engine == "tesseract" else [None]
                    for psm in psms:
                        setting = f"{engine}_{preset}" + (f"_psm{psm}" if psm is not None else "")
                        folder = root / "runs" / job["id"] / str(image["index"]) / setting
                        folder.mkdir(parents=True, exist_ok=True)
                        texts, records = [], []
                        for part_no, (top, bottom) in enumerate(parts, 1):
                            part_file = folder / f"part_{part_no:02d}.png"
                            source.crop((0, top, source.width, bottom)).save(part_file)
                            start = time.monotonic()
                            try:
                                value = ocr_tesseract(part_file, psm, lang) if engine == "tesseract" else ocr_paddle(part_file)
                                error = None
                            except Exception as exc:
                                value, error = "", f"{type(exc).__name__}: {exc}"
                            (folder / f"part_{part_no:02d}.txt").write_text(value, encoding="utf-8")
                            texts.append(value.rstrip("\n"))
                            records.append({"part": part_no, "top": top, "bottom": bottom,
                                            "seconds": round(time.monotonic() - start, 3), "error": error})
                        # Raw join is intentional: overlap removal is a later, measurable step.
                        (folder / "raw.txt").write_text("\n".join(texts) + "\n", encoding="utf-8")
                        (folder / "merged_exact.txt").write_text(merge_exact_overlap(texts), encoding="utf-8")
                        dump(folder / "run.json", {"job_id": job["id"], "image_index": image["index"],
                                                   "image_sha256": image["sha256"], "engine": engine,
                                                   "preset": preset, "crop_height": crop_height,
                                                   "overlap": overlap, "psm": psm, "language": lang,
                                                   "versions": versions,
                                                   "parts": records})
                        print(f"{job['id']}/{image['index']} {setting}: {len(parts)} parts")


def evaluate(root: Path, split: str | None = None) -> None:
    selected = sample_ids(root, split)
    from rapidfuzz.distance import Levenshtein

    rows = []
    for run_file in (root / "runs").glob("*/*/*/run.json"):
        meta = load(run_file)
        if selected is not None and meta["job_id"] not in selected:
            continue
        truth_file = root / "truth" / meta["job_id"] / f"{meta['image_index']}.txt"
        if not truth_file.exists():
            continue
        truth = truth_file.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        for output_name in ("raw", "merged_exact"):
            prediction = (run_file.parent / f"{output_name}.txt").read_text(encoding="utf-8").replace("\r\n", "\n").strip()
            operations = Levenshtein.editops(truth, prediction)
            kinds = {name: sum(op.tag == name for op in operations) for name in ("delete", "insert", "replace")}
            true_words, pred_words = truth.split(), prediction.split()
            word_distance = Levenshtein.distance(true_words, pred_words)
            norm_truth = re.sub(r"\s+", " ", truth)
            norm_prediction = re.sub(r"\s+", " ", prediction)
            critical_file = truth_file.with_suffix(".critical.json")
            critical = load(critical_file).get("phrases", []) if critical_file.exists() else []
            found = [phrase for phrase in critical if re.sub(r"\s+", " ", phrase) in norm_prediction]
            rows.append({"job_id": meta["job_id"], "image_index": meta["image_index"],
                         "image_sha256": meta["image_sha256"], "setting": run_file.parent.name,
                         "output": output_name, "exact": truth == prediction,
                         "characters": len(truth), "character_errors": len(operations),
                         "missed_characters": kinds["delete"], "extra_characters": kinds["insert"],
                         "wrong_characters": kinds["replace"],
                         "cer": round(len(operations) / max(1, len(truth)), 5),
                         "normalized_cer": round(Levenshtein.distance(norm_truth, norm_prediction) / max(1, len(norm_truth)), 5),
                         "words": len(true_words), "word_errors": word_distance,
                         "wer": round(word_distance / max(1, len(true_words)), 5),
                         "critical_phrases": len(critical), "critical_found": len(found),
                         "critical_missed": [phrase for phrase in critical if phrase not in found],
                         "errors": [p for p in meta["parts"] if p["error"]]})
    rows.sort(key=lambda x: (x["cer"], x["wer"], x["setting"]))
    report = f"evaluation_{split}.json" if split else "evaluation.json"
    dump(root / report, {"schema": 1, "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
                                    "note": "Exact transcription; raw and exact-overlap merge evaluated separately",
                                    "results": rows})
    for r in rows:
        print(f"{r['job_id']}/{r['image_index']} {r['setting']} {r['output']}: CER {r['cer']:.3%}, WER {r['wer']:.3%}")
    print(f"Evaluated {len(rows)} runs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export-db", "export-candidates", "collect", "candidates", "select", "run", "evaluate"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--engines", default="tesseract")
    parser.add_argument("--presets", default="full,current,model_native")
    parser.add_argument("--psms", default="3,6,11")
    parser.add_argument("--lang", default="kor+eng")
    parser.add_argument("--limit", type=int, default=0, help="Maximum jobs for export-db; 0 means all")
    parser.add_argument("--tune", nargs="+", help="Job IDs for adjusting OCR settings")
    parser.add_argument("--holdout", nargs="+", help="Job IDs reserved for final verification")
    parser.add_argument("--split", choices=["tune", "holdout"], help="Fixed sample group for run or evaluate")
    args = parser.parse_args()
    if args.command == "export-db":
        export_db_manifest(args.root, args.limit)
    elif args.command == "export-candidates":
        export_candidate_manifest(args.root, args.limit)
    elif args.command == "collect":
        if not args.manifest:
            parser.error("collect needs --manifest")
        collect(args.manifest, args.root)
    elif args.command == "candidates":
        list_candidates(args.root)
    elif args.command == "select":
        if not args.tune or not args.holdout:
            parser.error("select needs --tune and --holdout")
        select_sample(args.root, args.tune, args.holdout)
    elif args.command == "run":
        engines = args.engines.split(",")
        presets = args.presets.split(",")
        if set(engines) - {"tesseract", "paddle"} or set(presets) - set(PRESETS):
            parser.error("Unknown engine or preset")
        run(args.root, engines, presets, [int(x) for x in args.psms.split(",")], args.lang, args.split)
    else:
        evaluate(args.root, args.split)


if __name__ == "__main__":
    main()
