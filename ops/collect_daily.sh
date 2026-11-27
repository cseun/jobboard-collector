#!/usr/bin/env bash
# 하루 한 번 수집기를 돌립니다(cron이 부름). 직접 실행해도 됩니다: sudo bash ops/collect_daily.sh
# - 이미 돌고 있으면 겹쳐 실행하지 않음(flock) · 최대 2시간 · 로그는 ~/jobboard-collect.log (오래된 줄은 자동 정리)
# - 수집이 끝나면 이어서 ① 회사 정보(scripts.enrich_companies, COMPANY_MAX·기본 40) ② 마감 갱신(scripts.refresh_status, REFRESH_MAX·기본 150) ③ 빈칸 채우기(FILL_MAX·기본 10) ④ 지원 준비 요청 처리(PREP_MAX·기본 3). 각각 0이면 건너뜀. 회사 정보 단계는 번호 찾기(FIND_MAX·기본 30)·뉴스(NEWS_MAX·기본 30)도 함께 함
# - 환경변수로 바꾸기: COLLECT_ARGS="--apply --details --ai-images 20"  COLLECT_SOURCE="saramin,jobkorea"(기본. 원티드는 서버 IP를 막아서(403) 내 PC Chrome 예약으로 받음 → docs/LOCAL_WANTED_COLLECTION.md)  COLLECT_EMAIL=내@메일
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${COLLECT_LOG:-/var/log/jobboard-collect.log}"
ARGS="${COLLECT_ARGS:---apply --details --ai-images 20}"
SOURCE="${COLLECT_SOURCE:-saramin,jobkorea}"
EMAIL_OPT=""; [ -n "${COLLECT_EMAIL:-}" ] && EMAIL_OPT="--email ${COLLECT_EMAIL}"
touch "$LOG" 2>/dev/null || LOG="$HOME/jobboard-collect.log"
exec 9>/tmp/jobboard-collect.lock
if ! flock -n 9; then echo "[$(date '+%F %T')] 이미 실행 중이라 건너뜀" >> "$LOG"; exit 0; fi
{
  echo "=== $(date '+%F %T %Z') 시작: scripts.collect --source $SOURCE $ARGS ==="
  cd "$DIR" && timeout 7200 docker compose exec -T api python -m scripts.collect --source "$SOURCE" $ARGS $EMAIL_OPT
  rc=$?
  echo "=== $(date '+%F %T %Z') 끝 (종료 코드 $rc: 0 정상 · 2 사이트가 막아서 중단 · 124 시간 초과) ==="
  if [ "${COMPANY_MAX:-40}" != "0" ]; then
    echo "--- 회사 정보(최대 ${COMPANY_MAX:-40}곳) ---"
    timeout 3600 docker compose exec -T api python -m scripts.enrich_companies --apply --max "${COMPANY_MAX:-40}" --find-max "${FIND_MAX:-30}" --news-max "${NEWS_MAX:-30}"
  fi
  if [ "${REFRESH_MAX:-150}" != "0" ]; then
    echo "--- 마감 갱신(최대 ${REFRESH_MAX:-150}건) ---"
    timeout 3600 docker compose exec -T api python -m scripts.refresh_status --apply --max "${REFRESH_MAX:-150}"
  fi
  if [ "${FILL_MAX:-10}" != "0" ]; then
    echo "--- 빈칸 채우기(최대 ${FILL_MAX:-10}건) ---"
    timeout 3600 docker compose exec -T api python -m scripts.fill_gaps --apply --max "${FILL_MAX:-10}" $EMAIL_OPT
  fi
  if [ "${PREP_MAX:-3}" != "0" ]; then
    echo "--- 지원 준비 요청 처리(최대 ${PREP_MAX:-3}건) ---"
    timeout 3600 docker compose exec -T api python -m scripts.prep_jobs --apply --max "${PREP_MAX:-3}" $EMAIL_OPT
  fi
  docker compose exec -T api python -m scripts.collect --report 2
} >> "$LOG" 2>&1
# 로그가 커지면 최근 3000줄만 남김
[ "$(wc -l < "$LOG")" -gt 6000 ] && tail -n 3000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit "$rc"
