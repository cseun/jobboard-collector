# 📋 채용 보드 서버 (3단계: AI·데모 포함)

초대 코드로 가입한 사람만 로그인해서 **보드 화면 전체**를 쓰는 서버입니다. 공고는 모두 같이 보고, 내 정보·공고별 준비·제출 서류는 **계정별로 따로** 저장됩니다.

## 화면으로 보는 채용보드

[**GitHub에서 화면 슬라이드 넘겨 보기**](docs/tour/01-dashboard.md) · [PC 브라우저용 슬라이드](docs/product-tour.html)

[**제품 기획문서: 핵심 기능과 해결하려는 문제**](docs/PRODUCT_PLAN.md)

GitHub용 슬라이드는 각 페이지 아래의 **이전 화면/다음 화면** 링크로 8개 핵심 기능을 넘깁니다. PC용 슬라이드는 저장소를 내려받아 `docs/product-tour.html`을 브라우저에서 열면 **버튼 또는 ← → 방향키**로 이동할 수 있습니다. 각 장은 실제 화면의 주요 영역과 기능을 만든 이유, 사용 방법, 남는 결과를 함께 보여줍니다. 화면 속 공고 수·날짜·진행률은 촬영 시점의 예시입니다.

| 메뉴 | 하위 화면 | 하는 일 |
|---|---|---|
| 🏠 홈 | 대시보드 · 시작 가이드 · 오늘 할 일 | 준비 진행률을 확인하고 기본 정보를 채운 뒤 하루 과제를 관리합니다. |
| 🧭 매니저 | 취업 매니저 · 공고별 준비 · 상담사 | 목표를 점검하고, 한 공고의 요건부터 서류·면접·계획까지 연결해 준비합니다. |
| 🔍 공고 | 국내 공고 · 추천 · 해외 후보 · 내 주변 | 공고를 찾고 조건을 살피며 관심 공고를 표시합니다. 추천과 일치율은 입력한 내 기준을 바탕으로 계산됩니다. 내 주변에서는 관심 공고가 있는 회사·관심 회사·둘 중 하나를 골라 지도와 목록에서 볼 수 있습니다. |
| 🏢 회사 | 회사 정보 · 회사 비교 · 면접 후기 | 기업 정보와 여러 회사를 비교하고 공개된 면접 후기를 봅니다. |
| 📨 지원 | 지원 현황 | 지원 예정·지원 완료·전형 진행·결과를 공고별로 기록합니다. |
| 📝 준비 | 서류 준비 · 이력서·포트폴리오 · 지원서 입력 · 모의면접 · AI 모의면접 · 프로젝트 점검 · 역량 준비 · 자료실 | 기본 서류와 공고별 서류를 만들고 지원 정보를 재사용하며 면접을 연습합니다. |
| 🌱 나 | 나 알아가기 · 직무 이해·나의 정의 · 내 정보 · 내 기준 | 경험과 선호, 가진 기술, 원하는 직무·조건을 정리합니다. |
| ⚙️ 내 설정 | 수집 설정 · 설정·백업 · 추후 과제 · 도움말·의견 등 | 보드의 작동 방식과 데이터 관리 기능을 확인합니다. |

처음 사용하는 흐름은 **시작 가이드 → 내 정보·내 기준 입력 → 공고 탐색 → 공고별 준비 → 서류·면접 준비 → 지원 현황 기록**입니다. 보드의 `지원 완료`는 기록 기능이며 실제 지원서는 해당 채용 사이트에서 제출해야 합니다.

```
브라우저 ─HTTPS─▶ Caddy ─┬─ /, /board.html, /board.js …  → web/ 정적 파일
                         ├─ /api/*                        → FastAPI ─▶ PostgreSQL
                         └─ /_blob/<파일id>               → FastAPI ─▶ files 볼륨(업로드한 서류)
```

## 1. 무엇이 어떻게 나뉘나

| 구분 | 내용 | 누가 받나 |
|---|---|---|
| 공용 데이터 | 공고 510건, 기업정보, 면접 후기, 잡플래닛 | 로그인한 모두 |
| 본인 맞춤 자료 | 회사별 지원 준비 자료(`docs`), 회사별 모의면접 자료(`MOCK`), 내 프로젝트·학습 계획 | **관리자(본인)만** |
| 개인 데이터 | 내 정보, 공고별 준비, 자소서 소재, 제출 서류 파일 | 각자 **본인만**(관리자도 남의 것은 못 봄) |

⚠️ 보드 코드(`board.js`)와 시드 데이터에는 개인정보가 없습니다. 본인 관련 자료는 모두 `private/` 폴더에 모아 두었고, **별도 zip(`jobboard_private.zip`)** 으로 받습니다.

| 파일 | 내용 | 쓰이는 곳 |
|---|---|---|
| `private/board_private.js` | 내 프로젝트·학습 계획·회사별 모의면접 자료 | 관리자에게만 `/api/private/board.js`로 전달 |
| `private/docs.json` | 회사별 지원 준비 자료 | `import_seed`가 관리자 전용 키(`admin:docs`)로 DB에 저장 |
| `private/forbidden.txt` | 빌드 때 누출을 검사할 금지 단어 목록 | `build_board.mjs`, 테스트 |

**`private/` 폴더를 GitHub 등에 올리지 마세요**(`.gitignore`에 들어 있음). 포트폴리오용 공개 저장소에는 `jobboard.zip` 내용만 올리면 됩니다.

## 2. 처음 배포하는 경우

```bash
# 서버(Ubuntu)에서
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER   # 재접속 필요
unzip jobboard.zip && unzip jobboard_private.zip      # 같은 위치에서 풀면 jobboard/private/ 가 채워짐
cd jobboard
cp .env.example .env && nano .env        # DOMAIN, POSTGRES_PASSWORD (openssl rand -hex 24), APP_ENCRYPTION_KEY(.env.example 안의 생성 명령 참고)

docker compose up -d --build
docker compose exec api python -m scripts.import_seed
docker compose exec -it api python -m scripts.create_admin you@example.com
```

포트 80/443은 **오라클 콘솔(Security List)과 서버 iptables 두 곳 모두** 열어야 합니다.
접속: `https://내도메인` → 로그인 → 보드가 열립니다. 초대 코드는 `https://내도메인/admin.html`에서 만듭니다.

## 3. 1단계를 이미 올려 둔 경우 (업그레이드)

```bash
cd ~                                      # jobboard 폴더가 있는 위치
unzip -o jobboard.zip && unzip -o jobboard_private.zip   # 덮어쓰기(.env는 zip에 없어 그대로 유지)
cd jobboard
# ★ 3단계: 사용자 API 키를 암호화할 마스터 키를 .env 에 한 번만 추가 (이미 있으면 건너뜀)
grep -qE '^APP_ENCRYPTION_KEY=.+' .env || echo "APP_ENCRYPTION_KEY=$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" >> .env

docker compose up -d --build              # 새 테이블은 시작할 때 자동 생성
docker compose exec api python -m scripts.import_seed   # ★ 꼭 실행
```

⚠️ **`.env`의 `APP_ENCRYPTION_KEY`를 따로 백업하세요**(비밀번호 관리자 등). 잃어버리면 저장된 모든 API 키를 읽을 수 없어 각자 다시 등록해야 해요. 이 값이 없으면 AI 기능만 꺼진 채(안내 문구 표시) 나머지는 그대로 동작합니다.

★ `import_seed`를 다시 실행해야 1단계에서 모두에게 열려 있던 `docs`(본인 맞춤 지원 자료)가 **삭제되고, `private/docs.json`이 관리자 전용 키로 저장**됩니다.

## 4. 자주 쓰는 명령

| 하는 일 | 명령 |
|---|---|
| 로그 | `docker compose logs -f api` |
| 코드 수정 반영 | `docker compose up -d --build api` |
| 시드 다시 반영(중복 없이 갱신) | `docker compose exec api python -m scripts.import_seed` |
| DB 백업 | `docker compose exec -T db pg_dump -U jobboard jobboard \| gzip > ~/backups/db_$(date +%F).sql.gz` |
| 업로드 파일 백업 | `docker run --rm -v jobboard_files:/f -v ~/backups:/b alpine tar czf /b/files_$(date +%F).tgz -C /f .` |

⚠️ **DB와 업로드 파일을 둘 다** 백업하세요. 볼륨 이름은 `docker volume ls`로 확인하세요(폴더 이름이 `jobboard`가 아니면 접두사가 다릅니다).

## 5. 보드 화면을 다시 만들 때 (원본 보드가 바뀐 경우)

```bash
cd tools && npm install
node build_board.mjs <원본 보드.html> ..      # web/board.html, web/board.js, private/board_private.js 를 다시 만듦
```

- **빌드 뒤에 한 번 더:** `node tools/patch_todo_p1.mjs web/board.js && node tools/patch_todo_p2.mjs web/board.js && node tools/patch_todo_p3.mjs web/board.js && node tools/patch_todo_p4.mjs web/board.js && node tools/patch_chat_ui.mjs web/board.js && node tools/patch_chat_nav.mjs web/board.js && node tools/patch_paging.mjs web/board.js && node tools/patch_newflag.mjs web/board.js && node tools/patch_dates.mjs web/board.js && node tools/patch_newrule.mjs web/board.js && node tools/patch_collectlog.mjs web/board.js && node tools/patch_irrelevant.mjs web/board.js && node tools/patch_multisel.mjs web/board.js` — "오늘 할 일 시작하기"(시작일 저장·시작하기 카드·팝업 스타일, 반복 일정·일정별 수정·삭제, 💡 할 일 제안, 채팅 목록 화면 개편(`web/board.html` 스타일 포함))를 `web/board.js`에 넣습니다. 이미 들어 있으면 "이미 적용됨"으로 건너뛰니 여러 번 실행해도 안전합니다. 마지막 `patch_dates`는 공고 정보에 **등록일·최초 수집**을 보여 주고, 정렬에 '📅 등록일순'·'📥 최초 수집순'을 넣고, 신규 기준을 '최초 수집 7일 이내'로 바꿉니다(확인: `node tools/test_patch_dates.mjs`). (원본 보드가 이 기능을 아직 모르기 때문에 빌드를 새로 하면 사라져요.)
- 개인정보가 박힌 최상위 선언은 `build_board.mjs`의 `PRIVATE` 목록에 있습니다. 새로 생기면 여기에 추가하세요.
- 빌드 마지막에 **금지 단어 검사**(이름·회사명 등)를 하고, 하나라도 남아 있으면 실패합니다. 단어 목록은 `private/forbidden.txt`(한 줄에 하나)에서 고칩니다.

## 5-1. AI 연결 (3단계)

```
브라우저(보드) ──▶ POST /api/ai/sample ──▶ 서버가 내 키를 복호화 ──▶ ChatGPT(OpenAI) · Claude(Anthropic) · Gemini(Google) 중 "사용 중"인 곳
                      (키는 브라우저에 없음)        (복호화는 이 순간만)
```

| 항목 | 동작 |
|---|---|
| 키 등록 | 보드 **⚙️ 설정·백업 → AI 연결 → 내 AI 키**에서 ChatGPT·Claude·Gemini 중 쓸 곳의 카드를 펼쳐 **키만 붙여 넣기**. 서버가 그 서비스의 모델 목록을 조회해(토큰 비용 없음) 키를 확인하고 **암호화해서 저장**, 화면에는 끝 4자리만 표시 |
| 모델 선택 | 키를 넣으면 **쓸 수 있는 모델 목록을 서비스에서 받아 와** 기본·빠른 모델을 자동으로 고르고(예: Claude sonnet/haiku, ChatGPT gpt-5.x/mini, Gemini pro/flash-lite), 카드의 선택칸에서 바꿀 수 있음. 목록은 새로고침 가능. 새 모델이 나와도 코드 수정 없이 목록에 나타남 |
| 여러 서비스 | 세 곳 모두 키를 넣어 두고 **"○○로 답하기"** 버튼으로 어디로 답할지 고름(계정당 하나만 "사용 중"). 쓰던 서비스의 키를 지우면 남은 서비스로 자동 전환 |
| 암호화 | Fernet(AES + 변조 검출). 마스터 키는 `.env`에만 있고 DB에는 없음 → DB만 유출돼도 키를 못 읽음. 암호문은 계정 id에 묶여 다른 계정 행으로 복사해도 거부됨 |
| 비용 | 각자 **자기 키의 ChatGPT·Claude·Gemini 계정**에서 과금. 각 사이트에서 월 한도를 걸어 두길 권장 |
| 한도 | 계정당 분당 20회 / 하루 300회(`AI_DAILY_LIMIT`), 질문 글자 20만 자, 이미지 4장·장당 4MB. 호출 수·토큰 수만 기록하고 **질문·답변 내용은 저장하지 않음** |
| 이전 버전에서 올릴 때 | Claude 키 하나만 저장하던 계정은 **키를 다시 넣을 필요 없이** 처음 열 때 새 구조로 자동 이전(모델은 `AI_MODEL_QUICK`·`AI_MODEL_DEFAULT` 값으로 시작). 새 표(`user_ai_keys`)는 서버가 시작할 때 자동으로 만들어짐 |
| 키 없이 쓰기 | 설정에서 AI를 **복사·붙여넣기**로 바꾸면 키 없이 어떤 AI로든 사용(이미지 읽기 제외) |
| 음성 | 🔊 읽어 주기는 브라우저 음성, 🎙 말로 답하기는 브라우저 음성 인식(Chrome·Edge, 마이크 허용 필요). 음성은 브라우저 제공사 서버에서 글자로 바뀜 |
| 마스터 키 교체 | `APP_ENCRYPTION_KEY=새키,옛키` 로 두면 옛 키로 저장된 것도 읽힘 → 모두 다시 저장된 뒤 옛 키를 뺌 |

### 데모 계정 (선택)

`.env`에 `DEMO_ENABLED=true`를 넣고 `docker compose up -d` 하면 로그인 화면에 **"가입 없이 둘러보기"** 버튼이 생겨요.
- 방문자마다 **따로 만든 임시 계정**(데이터가 섞이지 않음), 기본 6시간 뒤 계정·데이터가 함께 삭제(`DEMO_HOURS`)
- AI·파일 올리기는 꺼짐, 문서 수 300개 제한, IP당 시간당 6회까지만 시작 가능
- 관리자 사용자 목록에는 보이지 않음. 수동 정리: `docker compose exec api python -m scripts.cleanup_demo`

## 6. API 한눈에

| 메서드 | 경로 | 설명 | 권한 |
|---|---|---|---|
| POST | `/api/auth/register` · `login` · `logout` | 초대 코드 가입 · 로그인 · 로그아웃 | 공개 |
| GET | `/api/me` | 내 정보 | 로그인 |
| GET | `/api/board-data` | 보드 데이터(공고+공용 데이터). `docs`는 관리자에게만 실제 값 | 로그인 |
| GET | `/api/jobs?q=&status=` · `/api/jobs/{id}` | 공고 검색·상세 | 로그인 |
| GET/PUT/DELETE | `/api/docs/get·list·set·add·delete` | 보드의 저장소(컬렉션/문서) | 컬렉션별 규칙(아래) |
| POST | `/api/files` | 파일 업로드(PDF·이미지·txt·md, 20MB, 계정당 300MB) | 로그인 |
| GET | `/_blob/{id}` | 파일 보기·받기 | 소유자 또는 공용 자료실 파일 |
| DELETE | `/api/files/{id}` | 파일 삭제 | 소유자 |
| GET | `/api/private/board.js` | 본인 맞춤 자료(비공개 코드) | **관리자** |
| POST/GET/DELETE | `/api/admin/invites` · GET `/api/admin/users` | 초대 코드·사용자 관리 | 관리자 |
| GET | `/api/ai/status` | 세 서비스의 연결 상태(끝 4자리)·선택한 모델·사용 중인 서비스·오늘 사용량 | 로그인 |
| PUT/DELETE | `/api/ai/providers/{openai\|anthropic\|gemini}/key` | 키 등록(모델 목록 조회로 검증 후 암호화 저장) · 삭제 | 로그인(데모 제외) |
| GET/PUT | `/api/ai/providers/{서비스}/models` | 이 키로 쓸 수 있는 모델 목록(`?refresh=true`로 새로 받기) · 기본·빠른 모델 저장 | 로그인(데모 제외) |
| PUT | `/api/ai/active` | 사용할 서비스 고르기 | 로그인 |
| POST | `/api/ai/sample` | AI 호출 중계(`kind`: json/text, 이미지 선택) | 로그인(데모 제외) |
| POST | `/api/auth/demo` · GET `/api/public-config` | 임시 데모 계정 시작 · 로그인 화면용 공개 설정 | 공개(`DEMO_ENABLED=true`일 때만 시작) |
| GET/PUT/DELETE | `/api/me/data/{key}` | 단순 개인 저장소(1단계에서 만든 것, 보드는 `/api/docs`를 씀) | 본인 |

**저장소(`/api/docs`) 접근 규칙**
- `data/users/<내 id>/…` : 본인만 읽기·쓰기 (관리자도 남의 것은 403)
- `pending/<내 id>/…` : 본인 읽기·쓰기, 관리자는 읽기만
- `feedback` : 누구나 쓰기, 읽기는 본인 것만(관리자는 전부)
- 그 밖의 공용 컬렉션(`library`, `prep` 등) : 로그인한 누구나 읽기, 쓰기는 관리자만

## 7. 지금 되는 것 / 아직 안 되는 것

| 기능 | 상태 |
|---|---|
| 로그인, 초대 코드, 계정별 데이터 분리 | ✅ |
| 공고·기업·면접 후기 등 보드 전 화면 | ✅ (모든 탭 이동 시 오류 없음을 브라우저로 확인) |
| 내 정보·공고별 준비 저장, 제출 서류 업로드·보기·삭제 | ✅ |
| 계정별 ChatGPT·Claude·Gemini 키 등록(암호화) + 모델 선택 + AI 상담사·나 알아가기·서류 준비·모의면접 평가·이미지 읽기 | ✅ (세 서비스를 흉내 내는 가짜 서버로 등록→모델 선택→호출 경로 검증. **실제 키로는 첫 사용 때 확인 필요** — 특히 서비스별 모델 이름 필터와 자동 선택) |
| ✅ 오늘 할 일 "시작하기": 시작하기를 누른 날(또는 고른 미래 날짜)이 1일차 · 시작일은 내 계정 문서 `todo-program`에 저장 · 시작 가이드와 오늘 할 일 화면 두 곳에서 시작 · 시작일 바꾸기(오늘·미래만) | ✅ 브라우저 자동 테스트 통과 (규칙 기반 반복·제안·상담사 연동은 다음 단계) |
| ✅ 반복 일정: 오늘만·매일·평일·요일 고르기·N일마다 · 항목마다 ⋯로 "그날만/앞으로 전체" 수정·삭제, 일시정지, 10초 안에 되돌리기 · 🔁 반복 일정 관리 · 규칙은 내 계정 문서 `todo-rules`에 저장 | ✅ 브라우저 자동 테스트 통과 (할 일 제안·상담사·채팅 연동은 다음 단계) |
| ✅ 💡 할 일 제안(규칙표 9개, AI 키 없이 동작): 관심 공고 마감 임박·밀린 일·면접/서류 통과 준비·지원 7일 경과 결과 확인·희망 직무 신규 공고 확인·낮은 완료율 점검·내 정보 빈 칸·3일 무기록·관심 공고 정리 → 카드에서 추가/수정 후 추가/오늘만/무시, 모두 추가, 10초 되돌리기, 무시 14일 · 하루 3개까지 위에 + 나머지는 제안함 · 기록은 `todo-suggest` | ✅ 브라우저 자동 테스트 통과 (상담사·채팅 연동과 AI 보강은 다음 단계) |
| ✅ 상담사·채팅 → 제안 카드 한 길: 상담사가 추천·계획한 할 일은 오늘 할 일에 바로 넣지 않고 제안함에 담김(승인해야 들어감, 시작 전에도 보임) · 채팅 확인 카드에 반복(오늘만·매일·평일) 선택, 말로 "매일 …"·"평일 …"도 알아들음, 승인 시 반복 규칙 생성·되돌리기 | ✅ 브라우저 자동 테스트 통과 (AI가 [[할일|반복|날짜|분|내용]] 태그를 쓰는 부분은 실제 키로 첫 사용 때 확인 필요) |
| ✅ 반복 일정 점검(일·월요일): 최근 2주 동안 나온 횟수 대비 한 횟수가 25% 이하인 반복 일정을 카드로 제안 → ⏸ 일시정지 / ⏹ 그만하기(지난 기록 보존) / 그대로 두기(14일 무시), 10초 되돌리기 | ✅ 브라우저 자동 테스트 통과 (가짜 시계로 월요일 확인) |
| ✅ 채팅 화면 개편: 제목 "내 취업 매니저"(+쓰는 AI 표시), 오늘의 추천 작업 카드, 바로가기 칩, 채팅방 목록(날짜·미리보기·삭제), 방 제목을 눌러 이름 바꾸기, 빈 방에서 빠른 시작 | ✅ 브라우저 자동 테스트 통과 |
| ✅ 이미지 공고 읽기 테스트 `scripts/test_image_read.py`: 관리자 계정에 등록한 키(활성 서비스, `--provider`로 변경)로 이미지 주소 몇 개를 읽어 **내용·토큰·비용 추정**을 출력(저장 안 함) · 서버가 이미지를 직접 내려받는지(상태·크기·시간)도 확인 · 긴 이미지는 900px 높이로 잘라 한 번에 전송(Pillow) · Gemini의 '생각 토큰'을 출력 토큰에 더해 기록하도록 `_call` 보완 | ✅ 자동 테스트 6개(자르기·단가·Gemini 생각 토큰·전체 흐름·키 없음) · 전체 111개 통과 · **실제 키로 호출은 서버에서 확인 필요** |
| ✅ 신규 표시 A안: '구분'에 신규가 있어도 **최종 확인일이 오늘부터 7일을 넘으면 신규로 세지 않음**(월/일만 있으면 올해로 보고, 확인일이 비어 있으면 신규 아님) · 국내·해외 공고 모두 적용 · `web/board.js` 한 파일만 올리면 됨 | ✅ 시트 1,582건 기준 1,454 → 1,397(대부분이 최근에 한꺼번에 들어와서 7일이 지나야 줄어듦) · 경계값 9가지 확인 |
| ✅ 국내 공고 목록 페이징: 쪽당 20·30·50·100개(기본 30, 선택 기억) · 목록 맨 아래에 쪽 이동 막대(쪽당 개수 선택 포함, 폭 520px 미만에서는 « » 숨기고 앞뒤 1쪽만) · 검색·필터·정렬을 바꾸면 1쪽으로 · 스타일은 JS가 넣어 `board.js` 한 파일만 올리면 됨 | ✅ 브라우저 자동 테스트 12개 통과 · 공고 1,582건 기준 목록 그리기 약 1,100ms → 약 55ms · 국내 공고 화면만 적용(추천·회사 화면은 그대로) |
| ✅ 채팅 AI 답변 끝 이동 버튼: AI가 `[[이동|화면키]]`를 쓰면 "○○ 열기" 버튼으로 바꿈(허용된 화면만·최대 2개·틀린 키는 무시) | ✅ 브라우저 자동 테스트 통과(가짜 AI) · 실제 AI가 형식을 지키는지는 써 봐야 알 수 있음 |
| 🎙 말로 답하기 · 🔊 질문 읽어 주기 | ✅ 브라우저 기능(Chrome·Edge). 실제 음성 인식은 자동 테스트 불가 → 직접 확인 필요 |
| 가입 없이 둘러보기(데모) | ✅ 선택 기능 |
| "지금 새로 수집하기"(Claude 예약 수집 연동) | ❌ Claude 환경 전용 기능이라 서버에서는 동작하지 않음 |
| 빈칸 목록·채우기 API(`/api/admin/gaps`·`/jobs/{id}/fill`) + PC 도우미 `tools/fill_gaps_client.py` + 텍스트 공고 필수·우대 규칙 파서 + 설정 화면 수집 기록 표 | ✅ 자동 테스트(서버 159개·보드 패치 3종) 통과 · 자세히는 `docs/DATA_SOURCES.md` 10-6-2~4 |
| 공고 날짜 칸(`등록일`·`최초 수집`) + 사람인·잡코리아·원티드 증분 수집기 `collect`(`--source`, 미리보기 기본, `--apply`·`--details`·`--ai-images N`) + 보드 정렬·신규 기준 | ✅ 자동 테스트(서버 141개·보드 패치 13개) 통과 · 실행 기록(`collect_runs`: 모델·설정·토큰·건수) · 매일 자동 실행 `ops/install_cron.sh` · 사람인 화면 구조는 실제 페이지로 확인했으나 **서버에서 첫 `--dry-run` 확인 필요** · 자세히는 `docs/DATA_SOURCES.md` 10장 |
| 서버 자동 빈칸 채우기 `fill_gaps`(규칙 파서 → Sonnet, 하루 10건) + 데이터 업데이트 최종 정리 | ✅ 자동 테스트 통과 · 매일 수집 뒤 자동 실행 · 자세히는 `docs/UPDATE_GUIDE.md` |
| 마감 갱신 `refresh_status` · 회사 정보 수집 `enrich_companies` (매일 수집 뒤 자동) | ✅ 자동 테스트(서버 191개) 통과 · 자세히는 `docs/UPDATE_GUIDE.md` · 서버 첫 실행에서 사이트별 확인 필요 |
| 회사 번호 이름으로 찾기 · 소개글 · 최근 뉴스(`enrich_companies`) + 🧰 지원 준비 서버 처리 `prep_jobs`(공고 본문·회사 정보·뉴스 → 서류 준비 자료 빈 칸 채움, 하루 3건) | ✅ 자동 테스트(서버 198개) 통과 · 서버 첫 실행에서 사이트별 확인 필요 · 로그인 필요한 후기·모의면접은 채팅으로 이어서 |
| 내 PC Chrome 예약으로 면접 후기·합격 자소서 예시 수집 → `GET /api/admin/review-targets` · `POST /api/admin/review-import`(관리자 전용, 덧붙이기만) | ✅ 자동 테스트(서버 202개) 통과 · 예약 1개 등록됨(매일 20:43) · 첫 실행은 직접 지켜보며 확인 · 자세히는 `docs/LOCAL_REVIEW_COLLECTION.md` |
| 데이터 흐름 한눈에 `docs/DATA_FLOW.md` · 배포·외부 공개 계획(GitHub Actions vs Jenkins, Oracle 프리티어 메모, 점검표) `docs/DEPLOY_PLAN.md` | 📄 문서(결정 중 항목 포함) |
| 구글 시트(CSV) → DB 공고 반영 `sync_sheet`: 새 공고 추가 · 이미 있는 공고는 접수 상태·마감·확인일만 갱신 · 미리보기 후 `--apply` · AI 토큰 0 | ✅ 자동 테스트 9개 + 실제 시트 3개 탭 대조(채용공고 510건 전부 이미 있음 · 추가수집 사람인 891 + 잡코리아·원티드 186건은 새 공고) · 서버 실행은 직접 확인 필요 |
| 위 동기화를 매일 자동으로 돌리기(cron), 공식 API·웹 수집기, AI로 조건·회사 정보 정리 | ❌ 다음 단계 |
| 이전 보드(아티팩트)에 저장해 둔 내 정보 옮기기 | ❌ 서버의 내 정보는 처음엔 비어 있음 |

## 7-1. 시트의 공고를 보드에 반영하기 (`sync_sheet`)

시트의 탭을 CSV로 받아 DB에 반영합니다. 머리글을 보고 모양을 자동으로 알아봅니다.

| 시트 / 탭 | 머리글의 특징 | 비고 |
|---|---|---|
| 지원 검토용 '자동정렬'·'미지원' | 기업명 + 직무명 | 지원 상태·적합도는 가져오지 않음 |
| 내 수집 시트 '채용공고' | 기업명 + 공고명·담당 업무 | 보드와 같은 열 → 접수 상태·마감 글자를 그대로 갱신 |
| 내 수집 시트 '추가수집_사람인' · '추가수집_잡코리아·원티드' | 기업명 + 공고명 | 새 공고 추가(근무지 열은 사람인 탭에 없음) |

탭이 여러 개면 **탭마다 CSV로 받아 한 번씩** 실행하세요. 먼저 **미리보기**로 몇 건이 바뀌는지 보고, `--apply`를 붙여야 저장됩니다.

```bash
# 1) 코드 반영(처음 한 번): api/scripts/sync_sheet.py 를 서버의 jobboard/api/scripts/ 에 올린 뒤
sudo docker compose up -d --build api        # 데이터(DB·업로드 파일)는 그대로, 코드만 새로 빌드

# 2) 미리보기 → 저장 (CSV는 서버 홈에 올려 둔 파일)
sudo docker compose exec -T api python -m scripts.sync_sheet - < ~/sheet.csv
sudo docker compose exec -T api python -m scripts.sync_sheet - --apply < ~/sheet.csv

# 주소로 바로 읽기(시트가 '링크가 있는 모든 사용자 보기'일 때만 됨)
sudo docker compose exec -T api python -m scripts.sync_sheet --url "https://docs.google.com/spreadsheets/d/<시트ID>/export?format=csv&gid=<탭ID>"
```

- 가져오는 것: 기업명 · 직무명 · 근무지 · 경력/학력 조건 · 마감 · 채용 사이트와 주소 · 원문 접수 상태 · 확인일
- **가져오지 않는 것:** 지원 상태 · 적합도 · 적합 이유 · 확인 내용(메모). 시트 작성자의 개인 판단이라 섞지 않습니다.
- 이미 보드에 있는 공고(같은 사람인·잡코리아·원티드 번호)는 접수 상태·마감·최종 확인만 새 값으로 바꾸고 비어 있는 칸만 채웁니다. **설명·필수·우대 등은 덮어쓰지 않습니다.**
- 회사·직무 이름은 같은데 주소만 다른 공고는 중복으로 보고 건너뛴 뒤 "이름이 같아 건너뜀"에 알려 줍니다.
- 새 공고는 구분이 `[신규·시트]`로 들어가고, 직무 분류·분야는 제목의 키워드로 짐작해 넣습니다(못 찾으면 미분류). 회사 설명·필수·우대는 비어 있습니다.
- 반영 후 보드를 새로고침(Ctrl+Shift+R)하면 보입니다. 여러 번 실행해도 중복이 생기지 않습니다.
- compose 파일을 따로 쓴다면(`-f compose.local.yml` 등) 위 명령의 `docker compose` 뒤에 그대로 붙이세요.

## 8. 개발·테스트

```bash
cd api && pip install -r requirements-dev.txt && python -m pytest -q    # 96개, SQLite 메모리 DB
```

개발용으로 정적 파일까지 한 번에 띄우려면(운영에서는 Caddy가 담당):
`WEB_DIR=../web COOKIE_SECURE=false uvicorn app.main:app --port 8000`
