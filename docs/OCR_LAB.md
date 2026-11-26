# 채용공고 이미지 전량 읽기 실험

## 실무 설계에서 중요한 구분

1. **발견**: 검색어, 사이트, 정렬 방식, 결과 순위, 공고 URL을 남긴다. 관련성 판정으로 이미지 수집을 먼저 줄이지 않는다. 실험 대상의 선정 기준은 수집 전에 정한다.
2. **원본 보존**: 공고의 이미지 전부를 내려받아 원본 바이트, SHA-256, 크기, URL, 수집 시각을 보존한다. 같은 해시의 파일은 재사용한다. 실험은 원본 파일만 읽는다. 현재 채용보드 수집기는 이미지 URL을 DB에 저장할 뿐 이미지 파일 자체는 저장하지 않는다.
3. **전량 전사**: 채용 관련 여부나 `필수`·`우대` 구역을 묻지 않고 글자를 순서대로 읽는다. 각 조각의 OCR 원출력, 조각 좌표, 병합 결과를 모두 남긴다. 글자 앞 아이콘의 의미는 일반 OCR만으로 검증할 수 없으므로 별도 정답과 시각 모델 평가 대상으로 둔다.
4. **사용자 정답**: 원본 이미지마다 사람이 확인한 UTF-8 전사본을 만든다. 모델 출력은 정답으로 쓰지 않는다. 글자 오류율(CER), 단어 오류율(WER), 빠진·추가된·잘못 읽은 글자 수, 완전 일치를 기록한다. 공백과 줄바꿈을 포함한 엄격 비교를 먼저 하고, 필요할 때 별도로 정규화 비교를 추가한다.
5. **구조화**: 전량 전사가 검증된 원본에 한해 별도의 `필수`, `우대`, `기술 스택` 정답을 만든다. 분류 정확도와 글자 정확도를 혼합하지 않는다. 각 값에는 원문 근거와 위치를 연결한다.

정확도 100%가 나오지 않아도 실험을 중단하지 않는다. 공고별로 빠진 글자의 위치와 원인을 확인하고 전처리, 조각 크기, 겹침, OCR 엔진, 병합 단계 가운데 한 변수만 바꿔 재시험한다. 100%가 나오더라도 다른 공고 유형과 작은 글씨, 복잡한 표, 아이콘이 많은 이미지로 재검증한다.

## 준비

- Python 3.10 이상, `Pillow`, `rapidfuzz`.
- Tesseract 실행 파일과 `kor`, `eng` 언어 데이터. Tesseract는 `--psm`으로 페이지 구획 방식을 바꿀 수 있다.
- PaddleOCR은 선택. 한국어 `PP-OCRv5` 모델을 사용한다. 설치 시 CPU/GPU 환경에 맞는 PaddlePaddle도 필요하다.
- 모델 및 라이브러리 버전을 실험 기록에 별도로 적는다. 같은 모델 이름도 설치 시점에 따라 달라질 수 있다.

```bash
python -m pip install Pillow rapidfuzz
tesseract --list-langs
```

## 실행

`ocr_lab_example.json`을 복사해 `id`, `posting_url`, 검색 조건, 이미지 `path` 또는 HTTPS `url`을 채운다. 여러 장이면 `images`에 모두 넣는다. 원본 수집에는 **공고 페이지 URL이 아니라 이미지 URL** 또는 로컬 이미지 파일을 사용한다. 채용보드 DB의 `이미지 주소` 목록을 이 형식으로 내보내면 된다.

현재 서버의 이미지 공고를 쓰려면 이 스크립트를 서버의 `jobboard/api/scripts/ocr_lab.py`에 복사한 다음 API 컨테이너에서 읽기 전용으로 목록을 내보낼 수 있다. 기본은 이미지 주소가 있는 공고 전체이고, `--limit 15`로 15건만 고를 수도 있다. 이 목록에는 과거 수집기가 **이미지 공고로 인식해 URL을 저장한 공고만** 들어간다. 아직 발견하지 못한 이미지 공고를 확인하려면 별도 검색·수집 감사가 필요하다.

```bash
docker compose exec -T api python -m scripts.ocr_lab export-db --root /srv/files/ocr_lab --limit 15
docker compose exec -T api python -m scripts.ocr_lab collect --manifest /srv/files/ocr_lab/db_manifest.json --root /srv/files/ocr_lab
```

`/srv/files`가 영구 볼륨인지 서버의 `docker-compose.yml`에서 확인한 뒤 사용한다. 서버로 스크립트를 복사하거나 실행하는 작업은 이 채팅에서는 수행하지 않았다.

```bash
python tools/ocr_lab.py collect --manifest tools/ocr_lab_example.json --root work/ocr_lab_data
python tools/ocr_lab.py candidates --root work/ocr_lab_data
```

`candidates.json`의 공고별 이미지 장수, 최대 높이와 세로 비율로 후보를 좁힌다. 파일을 열어 작은 글씨, 표·다단, 아이콘을 확인한다. DB의 `--limit`는 후보 수 상한이다. 아래 예시 ID를 실제 공고 ID로 바꿔 조정용과 검증용을 고정한다. 동일 공고나 같은 해시의 이미지가 두 그룹에 걸치면 저장이 거부된다. 레이아웃이 비슷한 공고는 한 그룹에 배치한다.

```bash
python tools/ocr_lab.py select --root work/ocr_lab_data --tune 101 102 --holdout 103
python tools/ocr_lab.py run --root work/ocr_lab_data --split tune --engines tesseract --presets full,current,model_native --psms 3,6,11
```

표본은 `sample.json`에 고정된다. 설정을 조정하는 동안 `--split tune`을 사용한다. 설정을 고른 뒤 동일한 설정으로 `--split holdout`을 실행한다. 표본을 바꾸려면 별도 실험 폴더를 사용한다.

`collection.json`은 `images/`에 저장된 해시 기반 원본을 가리킨다. `runs/<id>/<이미지번호>/<설정>/`에는 조각 PNG, 조각별 OCR 원출력, 단순 연결 `raw.txt`, 같은 경계 줄을 합친 `merged_exact.txt`, 설정과 실행 시간이 저장된다. 병합이 실제 반복 문구를 제거했는지는 원출력과 대조한다. `full`은 원본 전체, `current`는 900px·겹침 150px, `model_native`는 2,276px·겹침 150px이다.

정답은 `work/ocr_lab_data/truth/<id>/<이미지번호>.txt`에 작성한다. 여러 이미지의 글자를 합친 정답이 아니라 **각 이미지별 정답**이다. 아이콘은 전사 파일에서 명시적 표기(예: `[아이콘:전화]`)를 쓰고, 별도 표에 실제 의미와 추측 여부를 기록한다. 현재 스크립트는 아이콘 점수를 자동 계산하지 않는다.

지원 자격, 마감일, 급여처럼 반드시 보존해야 할 문구는 같은 폴더의 `<이미지번호>.critical.json`에 `{"phrases": ["경력 3년 이상", "2026.12.15 마감"]}` 형식으로 넣을 수 있다. 일반 CER가 낮아도 이 문구를 놓친 실행은 별도로 확인한다.

```bash
python tools/ocr_lab.py evaluate --root work/ocr_lab_data --split tune
```

`evaluation_tune.json` 및 `evaluation_holdout.json`에는 해당 그룹에서 정답이 있는 실행이 기록된다. `raw`와 `merged_exact`를 대조해 병합으로 오류가 줄었는지 확인한다. 엄격한 CER, 공백·줄바꿈 차이를 줄인 CER, OCR 오류가 난 조각을 함께 기록한다. 실험 결과는 실험 폴더에 저장된다.

## 다음 실험과 DB 반영 기준

- 먼저 글자가 많은 이미지, 작은 글씨, 여러 열·표, 긴 세로 이미지, 아이콘이 있는 이미지, 이미지 여러 장인 공고를 고르게 고정 표본으로 선정한다. 이후 새 표본은 별도 검증 집합으로 둔다.
- 첫 비교는 Tesseract의 전체 이미지와 900/150·2276/150 조각, PSM 3·6·11이다. 이 결과에서 누락 유형을 확인한 뒤 PaddleOCR 한국어 모델, 600/0·600/150, 이미지 확대·대비 조절, 시각 언어 모델을 추가한다. 모델과 설정을 한꺼번에 20개 돌리기보다 변수별 효과가 보이도록 실험표를 고정한다.
- 손실 지표와 처리 시간·비용을 함께 본다. 특히 회사명, 마감일, 자격 요건의 누락은 전체 CER이 낮아도 별도로 표시한다.
- 구조화 정답은 전사 정답과 따로 만든다. `필수`, `우대`, `기술 스택`에는 해당 문구의 원문 위치를 연결하고, 판정 기준 버전을 기록한다. 이후 여러 분류 모델과 프롬프트를 같은 전사본으로 비교한다.
- 검증을 통과한 결과를 DB에 넣을 때는 원본 이미지 해시, OCR 엔진·버전·설정, 전사본, 정답 버전, 평가 점수, 구조화 결과를 **서로 다른 레코드**로 보존한다. 현재 보드의 `필수`·`우대` 칸에 바로 덮어쓰지 않는다.

이 저장소에는 실제 채용공고 이미지와 사용자가 확인한 정답 전사본이 없으므로, 현재 정확도나 최적 설정을 주장할 수 없다.
