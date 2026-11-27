# 🔄 데이터 업데이트 방법 (2026-11-27 수정)

> **현재 운영 기준:** 11월 25일 자동 수집분 825건은 상세·회사 정보 검증이 부족해 삭제했습니다. 삭제 전 백업은 서버의 `/home/ubuntu/backup_before_recent_cleanup_2026-11-27.sql`입니다. 수동으로 정리한 셀트리온제약 `AI Enablement` 1건은 보존했습니다. 아래의 이전 수집량·자동화 설명은 당시 계획이며, 실제 스케줄 설치 및 실행 여부는 운영 서버에서 별도로 확인해야 합니다.

### 2026-11-27 실제 운영 상태

- 외부 주소: `https://129.225.169.60.sslip.io/board.html` (임시 도메인, HTTPS 인증서 검증 완료). SSH는 22번 포트만 사용합니다.
- 일일 수집: `jobboard-collect.timer`가 **매일 03:17 KST**에 `/home/ubuntu/jobboard/ops/collect_daily.sh`를 실행합니다. `ops/install_cron.sh`의 cron은 설치하지 않았으며 같은 작업을 중복 등록하지 마세요.
- 예약 설정은 `ops/jobboard-collect.service`에 있습니다. 현재 사람인 텍스트 공고의 상세·회사 정보·마감 상태를 검증해 저장하고, 이어서 **기존 회사 정보 최대 10곳을 갱신**합니다. 이미지 OCR과 서버 AI 제목 판정은 꺼져 있습니다. 회사명으로 번호 찾기 최대 10곳, 공고 마감 재확인 최대 50건, 빈칸 채우기 최대 10건을 매일 실행합니다. 빈칸 채우기는 원문 규칙 파서로 먼저 처리하고 남은 항목에 Haiku를 사용합니다. 뉴스·좌표·지원 준비는 이 예약에서 0으로 설정되어 있습니다.
- 기존 회사 정보 보강은 11월 27일 10곳을 먼저 실행해 새 회사 1곳·기존 회사 9곳이 정상 반영되는 것을 확인했습니다. 실행 전 DB 백업은 서버의 `/home/ubuntu/backup_before_company_enrich_2026-11-27.sql`입니다.
- 11월 27일 첫 예약 실행은 요청 상한 250건에 걸려 **0건 저장**됐습니다. 상한과 페이지 수를 보정한 다음 실제 공고 1개 검색어로 재검증해 17건을 저장했습니다. 대상 밖인 기계·현장 직무 5건과 실제 고용 회사가 아닌 채용대행사 공고 3건을 각각 백업 후 삭제해 **9건**을 남겼습니다. 다음 전체 예약 실행 결과는 `systemctl status jobboard-collect.service`, `tail -n 100 /var/log/jobboard-collect.log`, `docker compose exec -T api python -m scripts.collect --report 5`로 확인하세요.
- 11월 27일 센트리온 `rec_idx=55544533`을 원문·회사 주소 대조 후 수동 보강했습니다. 업무·필수·우대·복지·전형·급여·경력·근무지·회사 정보를 항목별로 저장하고 원문 전체를 보존했습니다. 마감은 원문 기준 **2027-01-10**으로 확인했습니다. 이전 DB 값은 `/srv/files/backup_sentrion_55544533_2026-11-27.json`에 백업했습니다. 사람인 마감 메타 태그가 없는 페이지는 본문의 `접수기간`을 읽도록 상태 파서를 보완했습니다.
- 같은 날 기존 공고 마감 갱신을 한 차례 실행해 지난 마감일 26건을 닫고, 20건을 원문 재확인해 10건을 마감·10건의 마감일을 수정했습니다.
- 코드 변경은 서버 파일과 GitHub `main`에 함께 반영합니다. 다만 **자동 배포는 아직 설정되지 않았습니다**. GitHub에 push하는 것만으로 운영 서버가 갱신되지는 않습니다.
- 제목 판정의 `△`는 보류·사람 확인 대상입니다. `--recheck`는 기존 `×`만 Sonnet으로 다시 판정하며 `△`를 자동 재판정하지 않습니다. 현재 예약에는 `--no-judge`가 있어 새 공고에 제목 AI 판정 자체를 실행하지 않습니다.

새 수집은 `--apply`에서 상세 본문·업무·필수 요건·회사 사업 정보·원문 마감 상태를 확인한 후에만 저장합니다. 현재 이 검증은 사람인 **텍스트 공고**에 적용됩니다. 이미지 공고 및 잡코리아 공고는 상세 검증 경로가 준비되기 전까지 저장에서 제외됩니다. 제외 사유는 실행 결과의 `품질 검증`에 기록됩니다. PC Chrome 목록 가져오기는 미리보기만 허용합니다.

저장된 공고의 `raw`에는 `담당 업무`, `필수`, `우대`, `복지`, `전형 절차`를 각각 담고, `원문 본문`은 별도로 보존합니다. 회사 상세도 `회사 정보` 아래 `사업내용`, `업종`, `대표자명`, `주소`, `홈페이지`, `설립일`, `업력`, `기업형태`, `매출액`, `사원수`, `복지`로 나누어 저장하며 `회사 정보 출처`를 함께 남깁니다. 원문에서 확인되지 않은 값은 추측해 채우지 않습니다.

> 아래는 원래 설계안입니다. 위의 **실제 운영 상태**를 현재 설정으로 보세요.

## 1. 매일 자동으로 도는 것 (서버 cron, 매일 03:17 KST)
```
ops/collect_daily.sh  (flock으로 겹침 방지 · 로그 /var/log/jobboard-collect.log)
 ├─ ① scripts.collect --source saramin,jobkorea --apply --details --ai-images 20          새 공고
 │     사람인·잡코리아·원티드 목록 → (아는 공고 5건 연속이면 멈춤) → 조건 필터 → 저장
 │     → 상세: 사람인 텍스트=규칙 파서 · 사람인 이미지=Sonnet 5.5(하루 20건, 못 읽은 건 다음 날 이어서) · 원티드=API 구조 그대로
 ├─ ② scripts.enrich_companies --apply --max 40 --find-max 30 --news-max 30   회사 정보
 │     0) 번호(csn) 없는 옛 공고의 회사 → 이름으로 사람인 기업 검색, 같은 회사로 확실한 것만 번호 채움(모호하면 후보만 남김)
 │     1) 회사(csn)를 사람인 기업정보 페이지에서 읽어 companies에 저장 + 주소 → 좌표(geo) + 공고의 빈 규모·사업 칸
 │     2) 소개글이 빈 회사 → 기업정보 안쪽 페이지의 한 줄 소개 + 기업비전  3) 접수 중 공고 회사의 최근 3년 뉴스(구글 뉴스 RSS, 제목·링크만)
 ├─ ③ scripts.refresh_status --apply --max 150                                마감 갱신
 │     (요청 0) 마감일 지난 접수 중 공고 → '마감' / 오래 안 본 접수 중 공고 150건을 다시 열어 삭제·마감·마감일 연장 반영
 ├─ ④ scripts.fill_gaps --apply --max 10                                      빈칸 채우기
 │     빈칸이 남은 접수 중 공고를 최신순 최대 10건: 규칙 파서(0원) → 남은 칸만 Sonnet(추측 금지)
 ├─ ⑤ scripts.prep_jobs --apply --max 3                                       지원 준비 요청 처리
 │     보드 🧰 '요청 기록' 버튼이 남긴 요청을 서버가 처리: 공고 본문 + 회사 정보 + 뉴스 → Sonnet이 직무 정보·지원 방법·자소서 문항·준비 팁 정리
 │     → 서류 준비 자료(admin:docs)의 **빈 칸만** 채움(직접 쓴 내용은 그대로) · 요청당 AI 1번 · 실패는 사유 기록 후 2번까지
 └─ 실행마다 collect_runs에 1줄(모드: 저장·회사 정보·마감 갱신·빈칸 채우기·지원 준비 / 모델·토큰·비용·건수) → 보드 ⚙️ 설정 → 📒 수집 기록
```
| 환경변수 | 기본 | 뜻 |
|---|---|---|
| `COLLECT_SOURCE` | `all` | `saramin,jobkorea,wanted` 중 고르기 |
| `COLLECT_ARGS` | `--apply --details --ai-images 20` | 수집 옵션 |
| `COMPANY_MAX` | `40` | 하루에 읽을 회사 수(0이면 건너뜀) |
| `REFRESH_MAX` | `150` | 하루에 다시 열어 볼 접수 중 공고 수(0이면 건너뜀) |
| `FILL_MAX` | `10` | 하루 빈칸 채우기 건수(0이면 건너뜀) |
| `FIND_MAX` | `30` | 하루에 이름으로 회사 번호를 찾아볼 회사 수 |
| `NEWS_MAX` | `30` | 하루에 뉴스를 모을 회사 수 |
| `PREP_MAX` | `3` | 하루 지원 준비 요청 처리 건수(0이면 건너뜀). 요청당 Sonnet 1번 |
| `COLLECT_EMAIL` | (없음) | AI 키를 가진 계정(없으면 첫 관리자) |

## 2. 정보 종류별 업데이트 방법
| 정보 | 누가·어떻게 | 주기 | 내가 할 일 |
|---|---|---|---|
| **새 공고**(사람인·잡코리아·원티드) | 서버 자동 수집 | 매일 | 없음 |
| 공고 상세(필수·우대·급여·전형·근무지) | 서버 자동: 규칙 파서 → AI → 원티드 API | 매일 | 없음 |
| 이미지 공고 | 서버 AI(하루 20건 한도, 밀린 건 다음 날) | 매일 | 없음 |
| **공고 빈칸 남은 것** | 서버 `fill_gaps`(하루 10건, 공고당 2번 시도) | 매일 | 2번 시도 후에도 빈 칸은 그대로 → 필요하면 아래 3번 |
| 직접 찾은 공고 | 구글 시트에 적고 `sync_sheet` 실행 | 필요할 때 | 시트 입력 + 명령 1줄 |
| **회사 정보**(설립·사원수·매출·업종·주소·복지·좌표·소개글) | 서버 자동(`enrich_companies`): 새 회사는 바로, 기존 회사는 90일마다 갱신. 번호 없는 옛 공고는 이름으로 찾음 | 매일 | 이름이 같은 회사가 여러 곳이라 확정 못 한 곳만 `--ambiguous`로 보고 확인 |
| **최근 3년 뉴스** | 서버 자동(구글 뉴스 RSS · 제목·날짜·언론사·링크만) | 30일마다 | 없음 |
| **면접 후기·합격 자소서 예시**(캐치·잡플래닛·자소설닷컴·사람인, 로그인 필요) | **내 PC Chrome 예약**(매일 20:43): 로그인된 내 Chrome으로 읽고 요약 → 보드 API로 덧붙임. 자세히는 `docs/LOCAL_REVIEW_COLLECTION.md` | 매일(최대 5곳, 같은 회사는 60일에 1번) | PC·앱·Chrome 켜기 + SSH 터널 열기 + 각 사이트에 로그인해 두기 |
| **🧰 지원 준비**(직무 정보·지원 방법·자소서 문항·팁) | 보드에서 요청 기록 → 서버가 새벽에 처리(`prep_jobs`) | 요청한 다음 새벽 | 버튼 1번. 로그인 필요한 후기·모의면접은 채팅으로 이어서 |
| **마감 갱신** | 서버 자동(`refresh_status`) | 매일 | 없음 |
| 잡플래닛·면접 후기(로그인 필요) | **내 PC에서 직접**, 내 계정 전용 저장 | 필요한 회사만 | 로그인은 내가 직접 |

## 3. 수동으로 돌릴 때 (서버에서)
```bash
cd ~/jobboard
# 수집: 미리보기(저장 안 함) → 숫자 확인 → 저장
docker compose exec -T api python -m scripts.collect --source saramin,jobkorea --judge     # 미리보기(제목 판정까지 보려면 --judge)
docker compose exec -T api python -m scripts.collect --source saramin,jobkorea --apply --details --ai-images 20
# 회사 정보 · 마감 갱신: 미리보기 → 저장
docker compose exec -T api python -m scripts.enrich_companies
docker compose exec -T api python -m scripts.enrich_companies --apply --max 40
docker compose exec -T api python -m scripts.refresh_status
docker compose exec -T api python -m scripts.refresh_status --apply --max 150
# 빈칸 채우기: 미리보기(AI 호출 없음) → 저장
docker compose exec -T api python -m scripts.fill_gaps
docker compose exec -T api python -m scripts.fill_gaps --apply --max 10
# 기존 공고 제목 판정 · 정리 (관련 없는 공고 걸러내기, 자세히는 DATA_SOURCES 11장)
docker compose exec -T api python -m scripts.judge_existing                  # 미리보기(저장 안 함) — ○△× 건수와 목록
docker compose exec -T api python -m scripts.judge_existing --apply --redo   # 새 기준으로 다시 판정해 태그 저장(삭제 아님)
docker compose exec -T api python -m scripts.judge_existing --apply --redo --recheck   # ×만 Sonnet이 한 번 더 확인(+$0.03)
docker compose exec -T api python -m scripts.cleanup_excluded --x --career 5            # 지울 목록 미리보기
docker compose exec -T api python -m scripts.cleanup_excluded --x --career 5 --apply    # 실제 삭제(먼저 pg_dump 백업)
# 판정 모델 비교(Jev 키는 명령에만 넣기)
docker compose exec -T -e TYPESAFE_API_KEY=키 api python -m scripts.judge_compare --models haiku,jev --db 300
# 기록 보기
docker compose exec -T api python -m scripts.collect --report 5
tail -n 80 /var/log/jobboard-collect.log
```
- 빈칸 규칙: 값이 없거나 `원문 확인 필요`·`미확인` 같은 자리표시인 칸. 우대의 `—`는 '없음'이라 빈칸이 아닙니다.
- 채운 곳은 공고의 `채움 기록`에 `규칙 파서` 또는 `서버 AI 모델명 날짜`로 남습니다. 이미 값이 있는 칸은 절대 덮어쓰지 않습니다.
- 특정 공고를 내가 직접 고치고 싶을 때(PC, SSH 터널 필요): `tools/fill_gaps_client.py list|fill` → `POST /api/admin/jobs/{id}/fill`(관리자 로그인).

## 4. 아직 자동화하지 않은 것 (알고 쓰기)
| 항목 | 현재 | 다음 후보 |
|---|---|---|
| 이름이 같은 회사가 여러 곳인 옛 공고 | 이름 + 근무지로도 하나로 못 고르면 **번호를 넣지 않고** 후보만 `companiesFind`에 남김(잘못된 회사 정보가 붙는 것보다 낫기 때문). `python -m scripts.enrich_companies --ambiguous`로 후보 확인 | 후보 중 맞는 것을 공고 `csn`에 직접 넣기(30일 뒤 다시 시도도 함) |
| 연혁 | 사람인 기업정보 페이지에 연혁 칸은 있지만 내용이 비어 있는 회사가 많음. 서버는 연혁을 모으지 않음 | 회사 홈페이지 연혁 페이지 읽기 |
| 국민연금·DART | **쓰지 않음**: 사람인 기업정보 페이지에 국민연금 기준 사원수·매출액·설립일이 이미 있어 중복. DART의 평균 연봉·근속연수는 상장사만 있음 | 필요해지면 DART만 추가(무료 키) |
| 잡플래닛·캐치·자소설닷컴·채팅방 후기(로그인 필요) | 서버는 안 읽음. **내 PC Chrome 예약**이 읽어 서버에 올림(비밀번호는 어디에도 저장 안 함). 터널이 닫힌 날·로그인이 풀린 사이트는 그날 건너뜀 | 예약이 사이트별 허용 확인 없이 끝까지 도는지는 첫 시험 실행으로 확인 필요 |
| 합격 자소서 예시·합격자 스펙·모의면접 질문 | 서버 자동 안 함(공개 예시 선별과 내 경험 연결이 필요). 지원 준비 요청 시 채팅 문장으로 이어서 | 모의면접 질문 자동 생성은 보드 모의면접 자료 형식(`MOCK`)을 서버 저장소로 옮긴 뒤 |
| "내 경험과 연결" | 내 정보가 필요해 서버가 안 함 | 내 정보(UPROF)를 서버 AI에 보내도 되는지 정한 뒤 |
| 잡코리아 상세 | `fill_gaps`가 빈칸일 때만 상세 페이지를 읽음(본문이 iframe이면 따라감). 서버 IP에서 읽히는지 첫 실행으로 확인 필요 | 잡코리아 상세 전용 파서 |
| 마감 감지의 한계 | `채용시 마감`·상시 공고는 날짜가 없어 **사이트에서 삭제되기 전까지** 접수 중으로 둠. 사람인·잡코리아는 마감일이 지난 뒤에도 페이지가 남으므로 날짜로 판단 | — |
| 외부 공개 API + Claude 예약 | **하지 않기로 함**: Claude 예약이 도는 클라우드는 허용 목록에 없는 사이트로 나가는 요청을 막아서(예: 403), 도메인을 열어도 호출할 수 없음. 서버가 직접 하는 방식으로 대체 | 필요해지면 허용 도메인 등록 가능 여부부터 확인 |

## 5. 문제가 생기면
| 증상 | 확인 | 조치 |
|---|---|---|
| 사이트가 막음(종료 코드 2·기록에 ⛔) | 📒 수집 기록의 `막힘` 칸 | 하루 쉬고 다음 날 자동 재시도. 반복되면 `--sleep 5 8`로 간격 늘리기 |
| AI 키 없음 | 로그의 `Claude(anthropic) 키가 없어요` | 보드 ⚙️ 설정 → AI 연결에서 키 등록 |
| 비용이 걱정됨 | 📒 수집 기록의 토큰·비용(수집·빈칸 채우기 모두 합산) | `--ai-images`·`FILL_MAX` 줄이기, `FILL_MAX=0`이면 빈칸 채우기 끔 |
| 특정 사이트 파서가 깨짐 | 목록 건수가 0인데 사이트에는 공고가 있음 | `api/scripts/collect_sources.py`의 해당 사이트 함수만 수정 |
| 마감 갱신이 '읽지 못함'만 나옴 | 📒 수집 기록의 메모 | 사이트 페이지 모양이 바뀐 것 → `collect_sources.py`의 `*_status` 수정 |
| 자동 실행 끄기 | — | `sudo bash ops/install_cron.sh --remove` |

## 6. 코드 올리는 방법 (zip)
- **누적본 zip 하나만** 올립니다(`jb_v13g.zip`처럼 최신 파일이 전부 들어 있음). 일부 파일만 든 zip을 올리면 이전 버전에서 바뀐 파일이 빠져 동작하지 않을 수 있어요.
- 내 PC 터미널에서 `scp -i ~/.ssh/<키> jb_vXX.zip ubuntu@<서버IP>:~` → 서버에서 `cd ~ && unzip -o jb_vXX.zip -d ~`(zip 안은 `jobboard/` 폴더로 시작) → `cd ~/jobboard && sudo docker compose up -d --build api`.
- `web/`(보드 화면)만 바뀌었으면 재시작 없이 브라우저 새로고침(Ctrl+Shift+R)만.
- 판정 기준(`title_judge.PROFILE`)을 바꿨다면 `judge_existing --apply --redo`로 기존 공고도 다시 판정.
- 보드 화면 패치 순서(`tools/build_board.mjs` 이후): … `patch_irrelevant.mjs` → `patch_multisel.mjs`. 전부 여러 번 실행해도 안전합니다.
