# 복수 직무 공고와 필수·우대 공란 점검

## 왜 한 건으로 저장되었나

기존 수집기는 사람인·잡코리아·원티드의 **원문 공고 번호**를 중복 방지 키로 썼습니다. 제목이 `신입/경력 수시채용`이면 상세에 여러 모집 직무가 있어도 공고 번호 한 개만 저장했습니다. 특히 잡코리아 수집기는 목록만 저장하고 상세 요강을 읽지 않아 직무별 조건을 얻지 못합니다.

직무별 분할은 원문의 각 행을 확인한 뒤 해야 합니다. 전체 공고에 공통인 조건을 특정 직무의 `필수`로 복사하거나, 화면에 보이지 않는 직무를 만들어서는 안 됩니다.

2026-11-25 마감 셀트리온제약 수시채용의 사용자 제공 모집분야 6개를 검토한 결과, 현재 수집 목표에 직접 맞는 직무는 **AI Enablement 1개**입니다. AI Scientist [약물설계]은 약물·단백질 연구와 석사 요건 중심이며 나머지 직무는 보안·임상·총무·신약개발입니다. 이 공고의 실제 분할 데이터는 공개 저장소에 넣지 않고 `private/`의 로컬 JSON으로 준비했습니다. 실행 전 DB에 저장된 원문 공고 키를 확인해야 합니다.

## 직무별로 저장하기

1. 공고 원문의 이미지 또는 표에서 **각 직무명·필수·우대·담당 업무·근무지**를 확인합니다. 이미지라면 PC에서 OCR 전사본과 원본을 대조합니다.
2. 다음 JSON을 PC에서 작성합니다. `parent_source_key`는 DB에 저장된 원본 공고 URL 또는 키입니다. 예시 문구는 실제 셀트리온제약 조건이 아닙니다.

```json
{
  "parent_source_key": "https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx=원문번호",
  "roles": [
    {
      "title": "로봇 SW 개발",
      "required": ["원문에서 확인한 필수 조건"],
      "preferred": ["원문에서 확인한 우대 조건"],
      "tasks": ["원문에서 확인한 담당 업무"],
      "location": "원문에서 확인한 근무지"
    }
  ]
}
```

3. 미리보기에서 선택된 직무와 제외된 직무를 확인한 후 저장합니다. `--apply`를 다시 실행해도 같은 직무 키는 갱신하므로 중복 생성되지 않습니다.

서버에서 `requirements_audit --company 셀트리온제약`의 `source_key`를 보고 JSON의 `parent_source_key`가 같은 공고를 가리키는지 확인합니다. 로컬 파일은 서버에서 `~/jobboard/roles.json` 등 임시 경로로 옮긴 후 아래 명령을 실행합니다.

```bash
cd ~/jobboard
sudo docker compose exec -T api python -m scripts.multi_role - < roles.json
sudo docker compose exec -T api python -m scripts.multi_role - --apply < roles.json
```

원본 공고는 DB에 남겨 출처를 보존하지만 보드 목록에서는 숨기고, 관련 직무의 개별 공고만 표시합니다. 각 직무는 독립된 준비·관심·지원 기록 키를 사용합니다. 다만 원본 합본 공고에 이미 적어둔 개인 지원 기록은 새 직무 기록으로 자동 복사하지 않습니다.

## 필수·우대가 빈 공고의 실제 건수

아래 명령은 **DB를 변경하지 않고** 전체/접수 중 공고의 필수 공란, 우대 공란, 둘 다 공란 건수를 출처별·원인 유형별로 집계합니다.

```bash
cd ~/jobboard
sudo docker compose exec -T api python -m scripts.requirements_audit
sudo docker compose exec -T api python -m scripts.requirements_audit --active-only
sudo docker compose exec -T api python -m scripts.requirements_audit --company 셀트리온제약
```

원인 유형은 수집 메타데이터로 추정합니다. `잡코리아: 상세 본문 미수집`, `이미지: AI 미실행/한도/실패`, `이미지: AI 읽음, 해당 항목 추출 못함`, `본문 있음: 구역 제목 불일치/항목 미기재`, `본문 없음: 상세 미실행/실패/시트 입력`으로 나옵니다. 각 공고의 정확한 이유는 원문과 실행 로그를 함께 확인해야 합니다.
