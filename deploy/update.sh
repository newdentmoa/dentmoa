#!/usr/bin/env bash
# 덴트모아 업데이트 — 최신 코드를 받아 다시 만들고 시작합니다.
#
# 저장소 폴더(~/dentmoa)에서 실행합니다:
#   sudo bash deploy/update.sh
#
# 데이터(data/)와 설정(deploy/.env)은 그대로 남습니다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  printf '\n오류: %s\n' "$*" >&2
  exit 1
}
compose() { docker compose -f "$REPO_DIR/deploy/docker-compose.yml" "$@"; }

[ "$(id -u)" -eq 0 ] || fail "관리자 권한이 필요합니다. 앞에 sudo 를 붙여 실행해 주세요:  sudo bash deploy/update.sh"
[ -f "$REPO_DIR/deploy/.env" ] || fail "설치가 아직 안 된 것 같습니다. 먼저 'sudo bash deploy/install.sh' 를 실행해 주세요."

# 코드 폴더 주인(보통 ubuntu)의 이름으로 git 을 실행한다 (root 로 실행하면 git 이 거부할 수 있다)
owner="$(stat -c %U "$REPO_DIR")"
git_as_owner() {
  if [ "$owner" = root ]; then git "$@"; else sudo -u "$owner" git "$@"; fi
}

if [ -z "${DENTMOA_UPDATE_PULLED:-}" ]; then
  echo "[1/4] 최신 코드 받기"
  before="$(git_as_owner rev-parse --short HEAD)"
  git_as_owner pull --ff-only ||
    fail "최신 코드를 받지 못했습니다. GitHub 토큰이 만료됐거나 서버에서 파일을 직접 고쳤을 수 있어요. docs/6-문제해결.md 의 '업데이트가 안 돼요'를 봐 주세요."
  after="$(git_as_owner rev-parse --short HEAD)"
  if [ "$before" = "$after" ]; then
    echo "  이미 최신입니다 ($after). 그래도 다시 시작합니다."
  else
    echo "  $before → $after"
    git_as_owner log --oneline -n 20 "$before..$after" | sed 's/^/    /'
    # 이 스크립트도 바뀌었을 수 있다 → 새 코드의 update.sh 로 나머지 단계를 이어서 한다
    DENTMOA_UPDATE_PULLED=1 exec bash "$REPO_DIR/deploy/update.sh" "$@"
  fi
fi

echo
echo "[2/4] 다시 만들고 시작하기 (몇 분 걸릴 수 있어요)"
compose pull --quiet caddy || true # 웹 서버(Caddy)의 보안 업데이트도 받는다. 실패해도 지금 것으로 계속
compose up -d --build || fail "다시 시작하지 못했습니다. 'sudo bash deploy/update.sh' 를 한 번 더 실행해 보세요."

echo
echo "[3/4] 서버 연결 지킴이 등록 (인터넷 연결이 끊기면 스스로 복구)"
bash "$REPO_DIR/deploy/netwatch.sh" install && echo "  등록했습니다 (2분마다 확인)."

echo
echo "[4/4] 쓰지 않는 옛 이미지 정리"
docker image prune -f >/dev/null || true

echo
echo "업데이트가 끝났습니다. 대시보드를 새로고침해 보세요."
echo "로그 보기: cd $REPO_DIR/deploy && sudo docker compose logs -f app   (나가기: Ctrl+C)"
