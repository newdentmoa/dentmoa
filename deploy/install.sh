#!/usr/bin/env bash
# 덴트모아 서버 설치 (Ubuntu 22.04 / 24.04)
#
# 저장소 폴더(~/dentmoa)에서 실행합니다:
#   sudo bash deploy/install.sh                서버 IP로 만든 무료 주소(…sslip.io) 사용
#   sudo bash deploy/install.sh 내도메인.com    내 도메인 사용 (도메인이 이 서버 IP를 가리켜야 함)
#   sudo bash deploy/install.sh auto           서버 IP가 바뀌었을 때 주소를 다시 만들기
#
# 여러 번 실행해도 괜찮습니다. 이미 끝난 단계는 건너뛰고, 있는 deploy/.env 는 덮어쓰지 않습니다.
set -euo pipefail

# 패키지 설치 중에 묻는 화면(서비스 재시작 확인 등)이 뜨지 않게
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/deploy/.env"
APP_UID=1000 # Dockerfile 의 dentmoa 사용자 번호
TOTAL=8

step() { printf '\n==== [%s/%s] %s ====\n' "$1" "$TOTAL" "$2"; }
info() { printf '  %s\n' "$*"; }
fail() {
  printf '\n오류: %s\n' "$*" >&2
  exit 1
}
compose() { docker compose -f "$REPO_DIR/deploy/docker-compose.yml" "$@"; }

# 새 서버는 켜진 뒤 몇 분 동안 자동 업데이트가 apt 를 붙잡고 있을 수 있다 → 끝날 때까지 기다린다
wait_apt() {
  local i
  for i in $(seq 1 60); do
    fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1 || return 0
    if [ "$i" -eq 1 ]; then info "서버가 자동 업데이트 중입니다. 끝날 때까지 기다립니다 (최대 5분)…"; fi
    sleep 5
  done
}

# deploy/.env 의 값 읽기
env_get() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

# deploy/.env 의 값 바꾸기 (없으면 끝에 추가)
env_set() {
  if grep -q "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$1" "$2" >>"$ENV_FILE"
  fi
}

[ "$(id -u)" -eq 0 ] || fail "관리자 권한이 필요합니다. 앞에 sudo 를 붙여 실행해 주세요:  sudo bash deploy/install.sh"
[ -f "$REPO_DIR/Dockerfile" ] || fail "덴트모아 폴더를 찾지 못했습니다. 'cd ~/dentmoa' 로 이동한 뒤 다시 실행해 주세요."
if ! grep -qi '^ID=ubuntu' /etc/os-release 2>/dev/null; then
  info "참고: Ubuntu 가 아닌 것 같습니다. Ubuntu 22.04/24.04 기준으로 만든 스크립트라 일부 단계가 실패할 수 있어요."
fi

# 주소 인자 정리 (https:// 나 끝의 / 를 붙여 넣어도 되게)
ARG="${1:-}"
ARG="${ARG#https://}"
ARG="${ARG#http://}"
ARG="${ARG%/}"
if [ -n "$ARG" ] && [ "$ARG" != "auto" ] && ! [[ "$ARG" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  fail "주소 형식이 올바르지 않습니다: $ARG  (예: sudo bash deploy/install.sh dentmoa.example.com)"
fi

# ──────────────────────────── 1. 도커 ────────────────────────────
step 1 "도커(Docker) 설치 확인"
if ! command -v curl >/dev/null 2>&1; then
  wait_apt
  { apt-get update -qq && apt-get install -y -qq curl >/dev/null; } || fail "curl 을 설치하지 못했습니다."
fi
if command -v docker >/dev/null 2>&1; then
  info "도커가 이미 설치되어 있습니다: $(docker --version)"
else
  info "도커를 설치합니다. 몇 분 걸릴 수 있어요…"
  wait_apt
  curl -fsSL https://get.docker.com | sh ||
    fail "도커를 설치하지 못했습니다. 5분쯤 뒤 같은 명령을 다시 실행해 주세요: cd ~/dentmoa && sudo bash deploy/install.sh"
fi
if ! docker compose version >/dev/null 2>&1; then
  info "docker compose 를 설치합니다…"
  wait_apt
  # 도커 공식 저장소의 이름은 docker-compose-plugin, Ubuntu 저장소의 이름은 docker-compose-v2
  { apt-get update -qq && { apt-get install -y -qq docker-compose-plugin || apt-get install -y -qq docker-compose-v2; } >/dev/null; } ||
    fail "docker compose 를 설치하지 못했습니다."
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker info >/dev/null 2>&1 || fail "도커가 실행되지 않습니다. 'sudo systemctl status docker' 로 상태를 확인해 주세요."
info "준비됨: $(docker compose version)"

# ──────────────────────────── 2. 스왑 ────────────────────────────
step 2 "메모리 보충(스왑) 확인"
mem_mb=$(($(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024))
if [ -n "$(swapon --show --noheadings 2>/dev/null)" ]; then
  info "스왑이 이미 켜져 있습니다."
elif [ "$mem_mb" -ge 2048 ]; then
  info "메모리가 충분합니다 (${mem_mb}MB). 스왑 없이 진행합니다."
else
  info "메모리가 ${mem_mb}MB 라서 2GB 스왑 파일(/swapfile)을 만듭니다. (브라우저를 쓰는 사이트 수집에 필요)"
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  fi
  chmod 600 /swapfile
  swapon /swapfile 2>/dev/null || { mkswap /swapfile >/dev/null && swapon /swapfile; } ||
    fail "스왑을 켜지 못했습니다."
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  info "스왑을 켰습니다 (서버를 다시 켜도 유지됩니다)."
fi

# ──────────────────────────── 3. 주소 ────────────────────────────
step 3 "대시보드 주소 정하기"
current="$(env_get DOMAIN)"
if [ -n "$ARG" ] && [ "$ARG" != "auto" ]; then
  DOMAIN="$ARG"
elif [ -n "$current" ] && [ "$ARG" != "auto" ]; then
  DOMAIN="$current"
else
  info "서버의 공인 IP 주소를 확인합니다…"
  ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null ||
    curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
  ip="$(printf '%s' "$ip" | tr -d '[:space:]')"
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] ||
    fail "서버의 공인 IP 주소를 알아내지 못했습니다. 잠시 뒤 다시 실행하거나, 주소를 직접 넣어 실행해 주세요 (예: sudo bash deploy/install.sh 3-35-123-45.sslip.io)"
  DOMAIN="${ip//./-}.sslip.io"
  info "서버 IP: $ip"
fi
info "대시보드 주소: https://$DOMAIN"

# ──────────────────────────── 4. 설정 파일 ────────────────────────────
step 4 "설정 파일(deploy/.env) 준비"
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO_DIR/deploy/env.example" "$ENV_FILE"
  env_set DOMAIN "$DOMAIN"
  env_set DENTMOA_BASE_URL "https://$DOMAIN"
  env_set DENTMOA_SECURE_COOKIES 1
  info "새로 만들었습니다: $ENV_FILE"
elif [ "$current" != "$DOMAIN" ]; then
  env_set DOMAIN "$DOMAIN"
  env_set DENTMOA_BASE_URL "https://$DOMAIN"
  info "주소를 바꿨습니다: ${current:-(없음)} → $DOMAIN  (다른 설정은 그대로 둡니다)"
else
  info "이미 있습니다. 그대로 씁니다."
fi
if [ -z "$(env_get DENTMOA_SETUP_CODE)" ]; then
  # 처음 비밀번호를 정할 때 필요한 6자리 코드 (주소를 먼저 알아낸 다른 사람이 가로채지 못하게)
  env_set DENTMOA_SETUP_CODE "$(printf '%06d' $(( $(od -An -N4 -tu4 /dev/urandom) % 1000000 )))"
fi
SETUP_CODE="$(env_get DENTMOA_SETUP_CODE)"
chmod 600 "$ENV_FILE"

# ──────────────────────────── 5. 데이터 폴더 ────────────────────────────
step 5 "데이터 폴더 준비"
mkdir -p "$REPO_DIR/data"
chown -R "$APP_UID:$APP_UID" "$REPO_DIR/data"
info "공고·설정이 저장되는 곳: $REPO_DIR/data"

# ──────────────────────────── 6. 빌드·시작 ────────────────────────────
step 6 "프로그램 만들고 시작하기"
info "처음에는 10~20분 걸릴 수 있어요. 글자가 계속 올라가면 정상입니다. 창을 닫지 말고 기다려 주세요."
compose up -d --build ||
  fail "프로그램을 만들거나 시작하지 못했습니다. 같은 명령을 한 번 더 실행해 보고, 그래도 안 되면 docs/6-문제해결.md 를 봐 주세요."

# ──────────────────────────── 7. 자동 백업 ────────────────────────────
step 7 "매일 자동 백업 등록"
# 서버 시계는 보통 UTC 이다: UTC 19:30 = 한국 시간 새벽 4시 30분
cat >/etc/cron.d/dentmoa-backup <<EOF
# 덴트모아: 매일 새벽(한국 시간 4시 30분) 데이터 백업, 최근 14개 보관 — install.sh 가 만든 파일
30 19 * * * root /bin/bash "$REPO_DIR/deploy/backup.sh" >>/var/log/dentmoa-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/dentmoa-backup
info "백업은 $REPO_DIR/backups 에 쌓입니다."

# ──────────────────────────── 8. 동작 확인 ────────────────────────────
step 8 "동작 확인"
info "프로그램이 켜지기를 기다립니다 (최대 3분)…"
app_ok=""
for _ in $(seq 1 36); do
  if compose exec -T app python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)" >/dev/null 2>&1; then
    app_ok=1
    break
  fi
  sleep 5
done
if [ -z "$app_ok" ]; then
  printf '\n최근 로그:\n' >&2
  compose logs --tail 40 app >&2 || true
  fail "프로그램이 켜지지 않았습니다. 위 로그를 확인하거나, 잠시 뒤 'sudo bash deploy/install.sh' 를 다시 실행해 주세요."
fi
info "프로그램이 켜졌습니다."

info "https 인증서를 받고 있는지 확인합니다 (최대 2분)…"
https_ok=""
for _ in $(seq 1 8); do # 한 번에 최대 15초 × 8번 = 2분
  if curl -fsS --connect-timeout 5 --max-time 10 "https://$DOMAIN/healthz" >/dev/null 2>&1; then
    https_ok=1
    break
  fi
  sleep 5
done

echo
echo "══════════════════════════════════════════════════════════════"
echo " 덴트모아 설치가 끝났습니다!"
echo "══════════════════════════════════════════════════════════════"
echo
echo " 휴대폰이나 컴퓨터 브라우저에서 아래 주소를 여세요:"
echo
echo "     https://$DOMAIN"
echo
echo " 1) 처음 화면에서 설치 코드 [ $SETUP_CODE ] 를 넣고 대시보드 비밀번호를 정합니다. (지금 바로 해 주세요)"
echo " 2) '계정·연결' 화면에서 모어덴·덴트포토 아이디·비밀번호를 넣고,"
echo "    텔레그램과 메일 알림을 설정합니다. (GitHub 의 docs/3-텔레그램-설정.md, docs/4-메일-설정.md)"
echo " 3) '알림 조건' 화면에서 받고 싶은 공고 조건을 고릅니다. (docs/5-사용법.md)"
echo
if [ -z "$https_ok" ]; then
  echo " ※ 서버 안에서 https 주소 확인이 아직 안 됩니다. 휴대폰에서 주소가 열리지 않으면:"
  echo "    - Lightsail 인스턴스의 '네트워킹' 탭 → IPv4 방화벽에"
  echo "      HTTP(80)와 HTTPS(443) 규칙이 있어야 합니다. 없으면 '규칙 추가'로 넣고, 아래 명령으로 웹 서버를 다시 시작하세요."
  echo "        cd $REPO_DIR/deploy && sudo docker compose restart caddy"
  echo "    - 인증서를 받는 데 몇 분 걸릴 수 있습니다. 5분쯤 뒤 다시 열어 보세요."
  echo "    - 웹 서버 기록 보기: cd $REPO_DIR/deploy && sudo docker compose logs --tail 50 caddy"
  echo
else
  echo " ※ 주소가 열리지 않으면 Lightsail 인스턴스의 '네트워킹' 탭 → IPv4 방화벽에"
  echo "   HTTP(80)와 HTTPS(443) 규칙이 있는지 확인해 주세요."
  echo
fi
echo " 로그 보기:  cd $REPO_DIR/deploy && sudo docker compose logs -f app   (나가기: Ctrl+C)"
echo " 업데이트:  cd $REPO_DIR && sudo bash deploy/update.sh"
echo
