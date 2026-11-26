"""이미지 공고 읽기 테스트 스크립트(scripts/test_image_read.py): 자르기, 단가 계산, 전체 흐름(내려받기·AI 호출은 가짜로 대체)."""
import base64
import io
import json

import pytest

from app import config, crypto
from app.db import SessionLocal
from app.models import User, UserAiKey
from app.routers import ai as ai_mod
from scripts import test_image_read as tir
from sqlalchemy import select

MASTER = base64.urlsafe_b64encode(b"m" * 32).decode()
GEMINI_KEY = "AIza" + "C" * 35 + "GHIJ"


def make_png(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def test_slice_long_image_into_three_with_overlap():
    parts, note = tir.slice_image(make_png(860, 2650), "image/png", 900)
    assert len(parts) == 3 and "3조각" in note
    from PIL import Image
    hs = [Image.open(io.BytesIO(d)).size for d, _ in parts]
    assert all(w == 860 for w, _ in hs)
    assert all(h <= 900 + 80 for _, h in hs)
    assert sum(h for _, h in hs) >= 2650        # 겹침 때문에 합이 원본 이상


def test_short_image_not_sliced():
    parts, note = tir.slice_image(make_png(860, 800), "image/png", 900)
    assert len(parts) == 1 and "자르지 않음" in note


def test_estimate_cost_known_and_unknown():
    assert tir.estimate_cost("anthropic", "claude-sonnet-5-5", 3300, 500) == pytest.approx(0.0116)
    assert tir.estimate_cost("gemini", "gemini-3.8-flash", 2400, 1000) == pytest.approx((2400 * 0.75 + 1000 * 3.75) / 1e6)
    assert tir.estimate_cost("gemini", "gemini-3.5-flash-lite", 1000, 1000) == pytest.approx((300 + 2500) / 1e6)
    assert tir.estimate_cost("anthropic", "claude-haiku-5-5", 3400, 1050) == pytest.approx((340 + 525) / 1e6)
    assert tir.estimate_cost("openai", "gpt-6-luna", 3400, 1050) == pytest.approx((340 + 525) / 1e6)
    assert tir.estimate_cost("openai", "gpt-6-luna-pro", 1, 1) is None     # 단가가 다른 모델은 섞이지 않게
    assert tir.estimate_cost("gemini", "gemini-9-unknown", 1, 1) is None


def test_gemini_call_counts_thinking_tokens(monkeypatch):
    class R:
        status_code = 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 2400, "candidatesTokenCount": 300, "thoughtsTokenCount": 700}}
    monkeypatch.setattr(ai_mod, "_http", lambda *a, **k: R())
    out = ai_mod._call("gemini", GEMINI_KEY, "gemini-3.8-flash", [{"role": "user", "text": "x"}], [], 4096)
    assert out["in"] == 2400 and out["out"] == 1000          # 출력 300 + 생각 700


def test_full_flow_with_fakes(admin_client, monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.is_admin.is_(True))).scalars().first()
        db.add(UserAiKey(user_id=u.id, provider="gemini", ciphertext=crypto.encrypt(u.id, GEMINI_KEY), last4="GHIJ",
                         model_default="gemini-3.8-flash", model_quick="", active=True))
        db.commit()

    monkeypatch.setattr(tir, "fetch_image", lambda url: (make_png(860, 2650), "image/png", {"status": 200, "bytes": 1234, "content_type": "image/png", "sec": 0.1}))
    seen = {}

    def fake_call(provider, key, model, msgs, images, max_tokens):
        seen.update(provider=provider, key=key, model=model, n=len(images), max_tokens=max_tokens)
        return {"text": '```json\n{"모집분야": "펌웨어", "필수": ["학사"], "읽지못한부분": []}\n```', "truncated": False, "in": 2400, "out": 600}
    monkeypatch.setattr(ai_mod, "_call", fake_call)

    lines = []
    out = tir.run(["https://example.com/a.png"], say=lines.append)
    assert seen["provider"] == "gemini" and seen["key"] == GEMINI_KEY and seen["n"] == 3 and seen["max_tokens"] == 4096
    assert out["parsed"]["모집분야"] == "펌웨어" and out["tokens_in"] == 2400 and out["tokens_out"] == 600
    assert seen["model"] == "gemini-3.5-flash-lite"            # 저장돼 있던 3.8-flash는 허용 목록 밖이라 flash-lite로 대체
    assert out["cost_usd"] == pytest.approx((2400 * 0.30 + 600 * 2.50) / 1e6)
    assert any("3조각" in l for l in lines) and any("비용 추정" in l for l in lines)
    assert GEMINI_KEY not in "\n".join(lines)               # 키가 출력에 나오면 안 됩니다


def test_no_key_gives_clear_message(admin_client):
    with pytest.raises(SystemExit) as e:
        tir.run(["https://example.com/a.png"])
    assert "AI 키가 없어요" in str(e.value)


def test_raw_error_is_shown_and_key_hidden(admin_client, monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.is_admin.is_(True))).scalars().first()
        db.add(UserAiKey(user_id=u.id, provider="gemini", ciphertext=crypto.encrypt(u.id, GEMINI_KEY), last4="GHIJ",
                         model_default="gemini-3.8-flash", model_quick="", active=True))
        db.commit()
    monkeypatch.setattr(tir, "fetch_image", lambda url: (make_png(860, 800), "image/png", {"status": 200, "bytes": 1, "content_type": "image/png", "sec": 0.1}))

    class R:
        status_code = 503
        text = '{"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}}'
        def json(self):
            return json.loads(self.text)
    monkeypatch.setattr(ai_mod, "_http", lambda *a, **k: R())
    orig = ai_mod._map_error
    lines = []
    try:
        with pytest.raises(ai_mod._AiError):
            tir.run(["https://example.com/a.png"], say=lines.append)
    finally:
        ai_mod._map_error = orig
    joined = "\n".join(lines)
    assert "HTTP 503" in joined and "overloaded" in joined and GEMINI_KEY not in joined


def test_list_models_prints_names(admin_client, monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.is_admin.is_(True))).scalars().first()
        db.add(UserAiKey(user_id=u.id, provider="gemini", ciphertext=crypto.encrypt(u.id, GEMINI_KEY), last4="GHIJ",
                         model_default="gemini-3.8-flash", model_quick="", active=True))
        db.commit()
    monkeypatch.setattr(ai_mod, "_list_models", lambda p, k: ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
    lines = []
    tir.run([], say=lines.append, list_models=True)
    assert "  gemini-3.5-flash-lite" in lines


def test_jpeg_option_shrinks_and_keeps_slices():
    png = make_png(860, 2650)
    parts, note = tir.slice_image(png, "image/png", 1400, jpeg=85)
    assert len(parts) == 2 and all(m == "image/jpeg" for _, m in parts) and "JPEG 85" in note
    from PIL import Image
    assert all(Image.open(io.BytesIO(d)).format == "JPEG" for d, _ in parts)
    whole, note2 = tir.slice_image(png, "image/png", 0, jpeg=85)
    assert len(whole) == 1 and whole[0][1] == "image/jpeg"


def test_claude_effort_and_thinking_options_reach_request_body(monkeypatch):
    seen = {}

    class R:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}}
    monkeypatch.setattr(ai_mod, "_http", lambda provider, method, url, key, **k: (seen.update(body=k.get("body")), R())[1])
    ai_mod._call("anthropic", "sk-ant-" + "x" * 30, "claude-haiku-5-5", [{"role": "user", "text": "hi"}], [], 4096,
                 {"output_config": {"effort": "low"}, "thinking": {"type": "disabled"}})
    assert seen["body"]["output_config"] == {"effort": "low"} and seen["body"]["thinking"] == {"type": "disabled"}
    ai_mod._call("anthropic", "sk-ant-" + "x" * 30, "claude-haiku-5-5", [{"role": "user", "text": "hi"}], [], 4096)
    assert "output_config" not in seen["body"] and "thinking" not in seen["body"]     # 안 쓰면 요청이 그대로


def test_apply_corrections_walks_nested_json_and_reports_missing():
    data = {"복지": ["새내근로 복지기금", "자녀 학자금"], "기타안내": ["상기 복리후생은 새내 기준에 따라"], "급여": ""}
    fixes = [{"원문": "새내근로", "수정": "사내근로", "이유": "x"}, {"원문": "새내 기준", "수정": "사내 기준", "이유": "x"},
             {"원문": "없는말", "수정": "있는말", "이유": "x"}, {"원문": "같음", "수정": "같음", "이유": "x"}, {"원문": "", "수정": "a"}]
    out, applied, missed = tir.apply_corrections(data, fixes)
    assert out["복지"] == ["사내근로 복지기금", "자녀 학자금"] and out["기타안내"] == ["상기 복리후생은 사내 기준에 따라"]
    assert [f["원문"] for f in applied] == ["새내근로", "새내 기준"] and [f["원문"] for f in missed] == ["없는말"]
    assert data["복지"][0] == "새내근로 복지기금"      # 원본은 바뀌지 않음


def test_proofread_second_pass_sends_text_only(admin_client, monkeypatch):
    monkeypatch.setattr(config, "APP_ENCRYPTION_KEY", MASTER)
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.is_admin.is_(True))).scalars().first()
        db.add(UserAiKey(user_id=u.id, provider="gemini", ciphertext=crypto.encrypt(u.id, GEMINI_KEY), last4="GHIJ",
                         model_default="gemini-3.5-flash-lite", model_quick="", active=True))
        db.commit()
    monkeypatch.setattr(tir, "fetch_image", lambda url: (make_png(860, 800), "image/png", {"status": 200, "bytes": 1, "content_type": "image/png", "sec": 0.1}))
    calls = []

    def fake_call(provider, key, model, msgs, images, max_tokens, **kw):
        calls.append({"n_images": len(images), "text": msgs[-1]["text"]})
        if len(calls) == 1:
            return {"text": '{"복지": ["새내근로 복지기금", "경조금"]}', "truncated": False, "in": 1000, "out": 200}
        return {"text": '{"정정": [{"원문": "새내근로", "수정": "사내근로", "이유": "존재하지 않는 단어"}], "의심": [{"원문": "경조금", "이유": "확실하지 않음"}]}',
                "truncated": False, "in": 300, "out": 80}
    monkeypatch.setattr(ai_mod, "_call", fake_call)
    lines = []
    out = tir.run(["https://example.com/a.png"], say=lines.append, proofread=True)
    assert calls[0]["n_images"] == 1 and calls[1]["n_images"] == 0              # 2단계는 이미지 없이 글자만
    assert "새내근로 복지기금" in calls[1]["text"]
    assert out["fixed"]["복지"] == ["사내근로 복지기금", "경조금"]                  # 확실한 것만 고치고 의심은 그대로
    assert out["proof"]["cost_usd"] is not None and any("합계 비용" in l for l in lines)
    assert any("의심만 함" in l and "경조금" in l for l in lines)
    assert GEMINI_KEY not in "\n".join(lines)
