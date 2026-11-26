# 🖥️ 내 PC Chrome으로 원티드 공고 수집하기 (예약)

원티드는 **서버(오라클) IP를 막습니다(HTTP 403).** 그래서 서버 수집(`COLLECT_SOURCE`)에서는 빼고, **내 PC의 Chrome**이 원티드에서 목록을 읽어 서버로 보냅니다. 서버는 받은 목록을 사람인·잡코리아와 같은 규칙(아는 공고·경력/학력/제외어·AI 제목 판정·저장)으로 처리합니다. 로그인이 필요 없어 비밀번호를 쓰지 않습니다.

## 흐름
```
예약(내 PC, Claude 데스크톱 앱) ─→ Chrome 탭 ① 보드(localhost:18080, SSH 터널) · 탭 ② wanted.co.kr
   ├─ ① GET  /api/admin/job-plan        읽을 목록 주소(키워드 27개 × 쪽) + 서버가 이미 아는 원티드 공고 번호(known)
   ├─ ② 원티드 목록 API를 키워드마다 읽고, known에 없는 공고의 핵심 칸(id·제목·회사·경력·고용형태)만 뽑음
   └─ ① POST /api/admin/job-import      뽑은 핵심 칸(items)을 보냄 → 서버가 필터·제목 판정·저장
```
- **왜 서버로 직접 안 보내나**: 탭 ②(wanted.co.kr)에서 localhost로 보내면 사이트가 달라 보드 로그인 쿠키가 따라가지 않습니다. 그래서 읽은 내용을 탭 ①에서 보냅니다(예약 작업이 중간에서 옮김). 양을 줄이려고 **아는 공고는 빼고 핵심 칸만** 옮깁니다.
- 목록 정보만 저장합니다(필수·우대 같은 상세 칸은 비어 있음 → 이후 `fill_gaps`가 하루 한도 안에서 채움).
- 키워드당 기본 3쪽(쪽당 20건), 한 쪽도 새 공고가 없으면 그 키워드는 거기서 멈춤.

## 켜 두어야 하는 것
| 조건 | 확인 |
|---|---|
| PC가 켜져 있고 Claude 데스크톱 앱·Chrome(확장 프로그램)이 열려 있음 | 예약 시각 전 |
| SSH 터널이 열려 있음(`ssh -L 18080:localhost:80 …`) | `localhost:18080/board.html`이 열리는지 |
| 보드에 관리자로 로그인된 상태 | 보드 탭에서 확인 |
| 원티드 로그인은 **필요 없음** | — |

## 지키는 규칙
- 요청 사이 4~6초 쉼. **403·429가 나오면 그날은 즉시 중단**(거기까지 읽은 것만 보냄).
- 로그인·캡차 화면이 나오면 아무것도 입력하지 않고 중단. 지원하기·북마크 등 어떤 버튼도 누르지 않음(읽기만).
- 보드 말고 다른 곳으로 데이터를 보내지 않음.

## 예약 프롬프트 (이 문장이 예약에 들어 있음. 바꾸려면 예약을 수정)
```
채용 보드 원티드 공고 수집(내 PC Chrome). 아래 순서대로 하고, 마지막에 결과를 표로 보고해 줘.

0) Claude in Chrome 도구를 불러오고, 새 탭 ①에서 http://localhost:18080/board.html 을 연다. 열리지 않으면(SSH 터널이 닫힘) "터널이 닫혀 있어 오늘은 건너뜀"이라고 보고하고 끝낸다.
1) 탭 ①에서 javascript로 fetch('/api/admin/job-plan?source=wanted&pages=3',{credentials:'same-origin'}).then(r=>r.json()) 를 호출한다. 401/403이면 "보드에 로그인이 필요함"이라고 보고하고 끝낸다(비밀번호는 입력하지 않는다). 결과의 keywords, known(아는 공고 번호), urls(keyword·page·url)를 기억한다.
2) 새 탭 ②에서 https://www.wanted.co.kr 을 연다(로그인 불필요). 탭 ②에서 javascript로 window.__known = new Set(known) 을 만든다(known은 1단계 값 그대로).
3) 키워드마다(1단계 keywords 순서) 탭 ②에서 javascript로 그 키워드의 urls를 쪽 번호 순서로 읽는다: fetch(url,{credentials:'include'}) → r.ok가 아니면(403·429) 거기서 전체를 중단. 응답 JSON의 data 배열에서 id가 window.__known에 없는 공고만 {id:String(d.id), t:d.position, c:(d.company||{}).name||'', f:d.annual_from, to:d.annual_to, e:d.employment_type||''} 로 뽑아 모으고 그 id를 window.__known에 넣는다. 한 쪽에서 새로 뽑힌 게 0건이면 그 키워드는 다음 쪽을 읽지 않는다. 쪽 사이에 4~6초(setTimeout) 쉰다. 그 키워드의 결과 배열만 돌려받는다.
4) 모은 결과(여러 키워드 합쳐 200건씩 끊어서)를 탭 ①에서 javascript로 fetch('/api/admin/job-import',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:'wanted',apply:true,items:[...]})}) 로 보낸다. 3단계에서 받은 배열을 글자 하나 바꾸지 말고 그대로 넣는다. 응답의 "새 공고"·"저장한 새 공고"·"조건으로 건너뜀"·"AI 제목 판정"(○·△·×)을 기록한다. 보낼 게 없으면 보내지 않는다.
5) 읽기 전용이다: 로그인·가입·지원·북마크·댓글 등 아무것도 누르거나 입력하지 않는다. 보드 말고 다른 곳으로 데이터를 보내지 않는다. 마지막에 키워드별 새로 뽑은 건수, 서버가 저장한 건수, ×로 거른 건수, 중단 이유(403·429·터널·로그인)를 표로 보고한다.
```

## 첫 실행은 지켜보기
- 이 방식은 **아직 실제 원티드·내 Chrome에서 검증하지 못했습니다.** 원티드 목록 API(`/api/chaos/search/v1/position`)가 내 Chrome의 같은 사이트 요청(fetch)으로 열리는지, 응답 칸 이름이 위와 같은지 첫 실행에서 확인하세요.
- 첫 실행은 아는 공고가 없어서 양이 많습니다(수백~천 건 · 옮기는 데 시간이 걸림). 이후에는 새 공고만 옮겨서 가벼워집니다.
- 안 열리면(403·빈 응답·칸 이름 다름) 이 문서에 결과를 남기고, 원티드는 보류하거나 수동으로 처리합니다.
