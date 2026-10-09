#!/usr/bin/env bash
# 덴트모아 데이터 백업
#   data/dentmoa.sqlite3 → backups/dentmoa-날짜-시각.sqlite3  (최근 14개만 남김)
#
# 저장소 폴더(~/dentmoa)에서 실행합니다:
#   sudo bash deploy/backup.sh
# install.sh 가 매일 새벽 자동으로 실행되도록 등록해 둡니다 (/etc/cron.d/dentmoa-backup).
#
# 프로그램이 켜져 있는 동안에도 안전하게 복사하도록 컨테이너 안에서 sqlite3 의 백업 기능을 쓴다.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

KEEP=14
STAMP="$(TZ=Asia/Seoul date +%Y%m%d-%H%M%S)" # 한국 시간
TMP_NAME=".backup-$STAMP.sqlite3" # 컨테이너가 /data(= 서버의 data/)에 먼저 만든다
DEST="backups/dentmoa-$STAMP.sqlite3"

fail() {
  printf '오류: %s\n' "$*" >&2
  exit 1
}
compose() { docker compose -f "$REPO_DIR/deploy/docker-compose.yml" "$@"; }

[ "$(id -u)" -eq 0 ] || fail "관리자 권한이 필요합니다. 앞에 sudo 를 붙여 실행해 주세요:  sudo bash deploy/backup.sh"
[ -f data/dentmoa.sqlite3 ] || fail "백업할 데이터가 없습니다 (data/dentmoa.sqlite3)."
[ -n "$(compose ps --status running -q app 2>/dev/null)" ] ||
  fail "덴트모아가 켜져 있지 않습니다. 'cd deploy && sudo docker compose up -d' 로 켠 뒤 다시 실행해 주세요."

trap 'rm -f "data/$TMP_NAME"' EXIT

compose exec -T app python -c '
import sqlite3, sys
src = sqlite3.connect("/data/dentmoa.sqlite3")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
dst.close()
src.close()
sys.exit(0 if ok else 1)
' "/data/$TMP_NAME" || fail "백업 파일을 만들지 못했습니다."

mkdir -p backups
[ -f backups/.gitignore ] || printf '*\n' >backups/.gitignore # 백업 파일이 깃에 올라가지 않게
mv "data/$TMP_NAME" "$DEST"
chmod 600 "$DEST"
echo "백업했습니다: $REPO_DIR/$DEST ($(du -h "$DEST" | cut -f1))"

# 오래된 백업 지우기 (이름에 날짜가 들어 있어 이름순 = 날짜순)
shopt -s nullglob
files=(backups/dentmoa-*.sqlite3)
if [ "${#files[@]}" -gt "$KEEP" ]; then
  old=("${files[@]:0:${#files[@]}-KEEP}")
  rm -f -- "${old[@]}"
  echo "오래된 백업 ${#old[@]}개를 지웠습니다 (최근 ${KEEP}개 보관)."
fi
