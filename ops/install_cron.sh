#!/usr/bin/env bash
# 매일 새벽 3시 17분(한국 시간)에 수집기를 돌리도록 등록합니다. 여러 번 실행해도 한 줄만 유지돼요.
#   등록:  sudo bash ops/install_cron.sh
#   해제:  sudo bash ops/install_cron.sh --remove
#   확인:  cat /etc/cron.d/jobboard-collect
set -eu
F="${CRON_FILE:-/etc/cron.d/jobboard-collect}"
[ "$(id -u)" = 0 ] || { echo "sudo로 실행해 주세요: sudo bash ops/install_cron.sh"; exit 1; }
if [ "${1:-}" = "--remove" ]; then rm -f "$F"; echo "✓ 자동 실행을 해제했어요"; exit 0; fi
DIR="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$DIR/ops/collect_daily.sh"
# 서버 시계가 UTC여도 '한국 시간 03:17'을 서버 시계로 바꿔서 적음
read -r MIN HOUR < <(date -d 'TZ="Asia/Seoul" 03:17' '+%M %H')
cat > "$F" <<EOF
# 채용 보드 수집기: 매일 한국 시간 03:17 (서버 시계로는 ${HOUR}:${MIN} / $(date +%Z))
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
${MIN#0} ${HOUR#0} * * * root $DIR/ops/collect_daily.sh
EOF
chmod 644 "$F"
echo "✓ 등록했어요: 매일 한국 시간 03:17 (서버 시계 ${HOUR}:${MIN} $(date +%Z))"
echo "  로그: /var/log/jobboard-collect.log   기록 보기: docker compose exec -T api python -m scripts.collect --report 7"
