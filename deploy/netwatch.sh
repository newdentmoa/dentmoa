#!/usr/bin/env bash
# 덴트모아 서버 인터넷 연결 지킴이 — cron 이 2분마다 실행한다 (/etc/cron.d/dentmoa-netwatch)
#
# 2026-10-10 Lightsail 서버에서, 서버는 멀쩡히 돌아가는데 IPv4 연결이 사라져 바깥과 끊긴 일이 있었다
# (amazon-ssm-agent 기록 'network is unreachable', 네트워크 프로그램 기록은 없음, 재부팅으로 복구).
# 같은 일이 생기면 스스로 고친다:
#   2번 연속(약 4분) 끊김  → 네트워크 프로그램(systemd-networkd)을 다시 시작
#   5번 연속(약 10분) 끊김 → 서버 재부팅 (켜진 지 20분이 지났고, 이 장치가 1시간 안에 재부팅한 적이 없을 때만)
# 끊기면 그때의 네트워크 상태를 /var/log/dentmoa-netwatch.log 에 남긴다(원인 찾기용).
# 이 기록은 영어로 쓴다 — Lightsail 브라우저 SSH 창은 한글이 깨져서 사진으로 읽기 어렵다.
#
#   sudo bash deploy/netwatch.sh install   2분마다 실행되도록 등록 (install.sh·update.sh 가 해 준다)
#   sudo bash deploy/netwatch.sh           지금 한 번 확인
#   cat /var/log/dentmoa-netwatch.log      기록 보기
set -euo pipefail
PATH="$PATH:/usr/sbin:/sbin" # cron 의 PATH 는 /usr/bin:/bin 뿐이다

# 시험(tests/test_deploy.py)에서 바꿀 수 있게 경로는 환경변수로 덮어쓸 수 있다
STATE="${NETWATCH_STATE:-/run/dentmoa-netwatch}" # /run 은 재부팅하면 비워진다
LOG="${NETWATCH_LOG:-/var/log/dentmoa-netwatch.log}"
LAST_REBOOT="${NETWATCH_LAST_REBOOT:-/var/lib/dentmoa-netwatch-last-reboot}"
UPTIME_FILE="${NETWATCH_UPTIME_FILE:-/proc/uptime}"
CRON_FILE="${NETWATCH_CRON_FILE:-/etc/cron.d/dentmoa-netwatch}"
# 클라우드(AWS·Lightsail) 안쪽 정보 주소 — 인터넷이 아니라 서버 바로 옆이라, 연결만 살아 있으면 늘 대답한다
META_URL="http://169.254.169.254/latest/meta-data/"
RESTART_AFTER=2
REBOOT_AFTER=5
MIN_UPTIME=1200 # 초
REBOOT_GAP=3600 # 초

log() {
  printf '%s %s\n' "$(date -u '+%F %T')" "$*" >>"$LOG"
  logger -t dentmoa-netwatch -- "$*" 2>/dev/null || true
}

# ──────────────────────────── 등록 ────────────────────────────
if [ "${1:-}" = install ]; then
  if [ "$(id -u)" -ne 0 ]; then
    printf '오류: 관리자 권한이 필요합니다. 앞에 sudo 를 붙여 실행해 주세요:  sudo bash deploy/netwatch.sh install\n' >&2
    exit 1
  fi
  self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  cat >"$CRON_FILE" <<EOF
# 덴트모아: 2분마다 서버 인터넷 연결을 확인하고, 끊겼으면 스스로 복구 — deploy/netwatch.sh 가 만든 파일
*/2 * * * * root /bin/bash "$self" >/dev/null 2>&1
EOF
  chmod 644 "$CRON_FILE"
  exit 0
fi

# ──────────────────────────── 확인 ────────────────────────────
net_ok() {
  [ -n "$(ip -4 route show default 2>/dev/null || true)" ] || return 1
  # 응답 코드는 상관없다(IMDSv2 만 켠 서버는 401). 연결만 되면 curl 은 0 으로 끝난다
  if curl -s -o /dev/null --max-time 5 "$META_URL"; then
    touch "$STATE/meta-seen"
    return 0
  fi
  # 이번 부팅에서 대답했던 곳이 대답을 안 하면 끊긴 것. 한 번도 대답한 적이 없으면(AWS 가 아닌 서버) 경로만 본다
  [ ! -e "$STATE/meta-seen" ]
}

snapshot() {
  {
    printf -- '--- %s network state\n' "$(date -u '+%F %T')"
    ip -4 addr show || true
    ip -4 route || true
    networkctl --no-pager list || true
    journalctl --no-pager -o short -n 25 -u systemd-networkd || true
    journalctl --no-pager -o short -k -n 15 || true
  } >>"$LOG" 2>&1
}

mkdir -p "$STATE"
fails="$(cat "$STATE/fails" 2>/dev/null || true)"
[[ "$fails" =~ ^[0-9]+$ ]] || fails=0

if net_ok; then
  if [ "$fails" -gt 0 ]; then
    log "network OK again (after $fails failed checks)"
  fi
  rm -f "$STATE/fails"
  exit 0
fi

fails=$((fails + 1))
echo "$fails" >"$STATE/fails"
log "network DOWN (check $fails)"
if [ "$fails" -eq 1 ]; then
  snapshot
fi

if [ "$fails" -eq "$RESTART_AFTER" ]; then
  log "restarting systemd-networkd"
  systemctl restart systemd-networkd || log "restart failed"
fi

if [ "$fails" -ge "$REBOOT_AFTER" ]; then
  uptime_s="$(cut -d. -f1 "$UPTIME_FILE")"
  now="$(date +%s)"
  last="$(cat "$LAST_REBOOT" 2>/dev/null || true)"
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  if [ "$uptime_s" -lt "$MIN_UPTIME" ] || [ $((now - last)) -lt "$REBOOT_GAP" ]; then
    # 막 켜졌거나 방금 재부팅했다 → 기다린다 (같은 말을 2분마다 쓰지 않게 다섯 번에 한 번만 기록)
    if [ $((fails % REBOOT_AFTER)) -eq 0 ]; then
      log "not rebooting yet (uptime ${uptime_s}s, last reboot by netwatch $((now - last))s ago)"
    fi
  else
    log "rebooting"
    snapshot
    echo "$now" >"$LAST_REBOOT"
    sync
    systemctl reboot
  fi
fi
