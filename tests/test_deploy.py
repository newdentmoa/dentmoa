"""배포 파일과 문서 점검 (도커·네트워크 없이).

- 스크립트 문법과 실행 권한
- Dockerfile·docker-compose·env.example 이 앱이 실제로 쓰는 이름과 맞는지
- README·docs 의 상대 링크와 #제목 링크가 살아 있는지
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
SCRIPTS = ["install.sh", "update.sh", "backup.sh", "netwatch.sh"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_syntax_and_executable(name):
    path = DEPLOY / name
    assert os.access(path, os.X_OK), f"{name} 에 실행 권한이 없음"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_dockerfile_contract():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "DENTMOA_DATA=/data" in text
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in text
    assert "playwright install --with-deps chromium" in text
    assert "fonts-noto-cjk" in text
    assert "http://127.0.0.1:8000/healthz" in text
    assert 'CMD ["python", "-m", "dentmoa", "serve"]' in text
    assert re.search(r"^USER dentmoa$", text, re.M)
    # install.sh 가 data 폴더 주인을 컨테이너 사용자 번호로 맞춘다
    uid = re.search(r"useradd --uid (\d+)", text).group(1)
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert re.search(rf"^APP_UID={uid}\b", install, re.M)


def test_compose_contract():
    text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    for needle in ("context: ..", "env_file: .env", "../data:/data", "shm_size: 512m", "image: caddy:2",
                   '"80:80"', '"443:443"', "./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy_data:", "caddy_config:",
                   "restart: unless-stopped", 'max-size: "10m"', 'max-file: "3"'):
        assert needle in text, needle
    # 앱이 실제로 읽는 환경변수 이름인지
    assert "DENTMOA_BEHIND_PROXY" in text
    assert "DENTMOA_BEHIND_PROXY" in (ROOT / "dentmoa" / "web" / "__init__.py").read_text(encoding="utf-8")
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "{$DOMAIN}" in caddy and "reverse_proxy app:8000" in caddy


def test_env_example_lists_every_setting():
    from dentmoa.settings_store import SECRET_FIELDS

    text = (DEPLOY / "env.example").read_text(encoding="utf-8")
    active = dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#"))
    assert set(active) == {"DOMAIN", "DENTMOA_BASE_URL", "DENTMOA_SECURE_COOKIES"}
    assert active["DENTMOA_SECURE_COOKIES"] == "1"
    assert active["DOMAIN"] == "" and active["DENTMOA_BASE_URL"] == ""  # install.sh 가 채운다
    for env in [*SECRET_FIELDS.values(), "ADMIN_PASSWORD"]:
        assert re.search(rf"^# {env}=", text, re.M), f"env.example 에 {env} 설명이 없음"


def test_healthz_is_public(tmp_db):
    from dentmoa import web

    client = web.create_app(testing=True).test_client()
    resp = client.get("/healthz")  # 비밀번호를 정하기 전에도, 로그인하지 않아도 열려야 한다
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "ok"


# ──────────────────────────── 문서 링크 ────────────────────────────

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def _slug(heading: str) -> str:
    """GitHub 이 제목에 붙이는 #주소."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    out = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code and (m := re.match(r"#{1,6}\s+(.+)", line)):
            out.add(_slug(m.group(1)))
    return out


def _doc_files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_docs_exist():
    names = {p.name for p in (ROOT / "docs").glob("*.md")}
    for n in ("1-서버-만들기.md", "2-설치하기.md", "3-텔레그램-설정.md", "4-메일-설정.md", "5-사용법.md",
              "6-문제해결.md", "7-개발-세션-설정.md"):
        assert n in names
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for n in sorted(names):
        assert f"docs/{n}" in readme, f"README 에 {n} 링크가 없음"


@pytest.mark.parametrize("doc", _doc_files(), ids=lambda p: p.name)
def test_doc_links(doc):
    for target in LINK_RE.findall(doc.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target):  # https:// 같은 바깥 주소
            continue
        file_part, _, anchor = target.partition("#")
        dest = (doc.parent / file_part).resolve() if file_part else doc
        assert dest.exists(), f"{doc.name}: 없는 파일 링크 {target}"
        if anchor and dest.suffix == ".md":
            assert anchor in _anchors(dest), f"{doc.name}: 없는 제목 링크 {target}"


# ──────────────────────────── 서버 연결 지킴이 (deploy/netwatch.sh) ────────────────────────────
# ip·curl·systemctl 같은 서버 명령은 가짜로 바꿔 끼우고, 2분마다 실행되는 것을 여러 번 불러서 흉내 낸다.

FAKE_COMMANDS = {
    # FAKE_ROUTE=1 이면 기본 경로가 있다
    "ip": 'if [ "$*" = "-4 route show default" ]; then [ "${FAKE_ROUTE:-1}" = 1 ] && echo "default via 172.26.0.1 dev ens5"; exit 0; fi\necho "ip $*"\n',
    # FAKE_META=1 이면 클라우드 안쪽 정보 주소가 대답한다
    "curl": '[ "${FAKE_META:-1}" = 1 ] && exit 0 || exit 7\n',
    "systemctl": 'echo "$*" >>"$FAKE_CALLS"\n',
    "logger": "exit 0\n",
    "networkctl": 'echo "networkctl $*"\n',
    "journalctl": 'echo "journalctl $*"\n',
}


@pytest.fixture
def netwatch(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash 없음")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKE_COMMANDS.items():
        f = bin_dir / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(0o755)
    uptime = tmp_path / "uptime"
    uptime.write_text("5000.00 100.00\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "NETWATCH_STATE": str(tmp_path / "state"),
        "NETWATCH_LOG": str(tmp_path / "netwatch.log"),
        "NETWATCH_LAST_REBOOT": str(tmp_path / "last-reboot"),
        "NETWATCH_UPTIME_FILE": str(uptime),
        "NETWATCH_CRON_FILE": str(tmp_path / "cron"),
        "FAKE_CALLS": str(tmp_path / "calls"),
    }

    class Runner:
        path = tmp_path

        def run(self, *args, **fake):
            e = {**env, **{k: str(v) for k, v in fake.items()}}
            subprocess.run(["bash", str(DEPLOY / "netwatch.sh"), *args], env=e, check=True)

        def calls(self):
            f = tmp_path / "calls"
            return f.read_text().splitlines() if f.exists() else []

        def log(self):
            f = tmp_path / "netwatch.log"
            return f.read_text() if f.exists() else ""

        def fails(self):
            f = tmp_path / "state" / "fails"
            return int(f.read_text()) if f.exists() else 0

    return Runner()


def test_netwatch_does_nothing_while_connected(netwatch):
    for _ in range(3):
        netwatch.run()
    assert netwatch.calls() == [] and netwatch.log() == "" and netwatch.fails() == 0


def test_netwatch_restarts_network_then_reboots(netwatch):
    # 2026-10-10 처럼 기본 경로가 사라졌다
    netwatch.run(FAKE_ROUTE=0)
    assert netwatch.fails() == 1 and netwatch.calls() == []
    assert "network DOWN (check 1)" in netwatch.log() and "network state" in netwatch.log()  # 원인 찾기용 기록
    netwatch.run(FAKE_ROUTE=0)
    assert netwatch.calls() == ["restart systemd-networkd"]
    netwatch.run(FAKE_ROUTE=0)
    netwatch.run(FAKE_ROUTE=0)
    assert netwatch.calls() == ["restart systemd-networkd"]
    netwatch.run(FAKE_ROUTE=0)  # 5번째(약 10분) → 재부팅
    assert netwatch.calls() == ["restart systemd-networkd", "reboot"]
    assert (netwatch.path / "last-reboot").read_text().strip().isdigit()


def test_netwatch_recovers_and_resets_count(netwatch):
    netwatch.run(FAKE_ROUTE=0)
    netwatch.run(FAKE_ROUTE=0)
    netwatch.run()
    assert netwatch.fails() == 0
    assert "network OK again (after 2 failed checks)" in netwatch.log()
    netwatch.run(FAKE_ROUTE=0)  # 다시 1번부터 센다
    assert netwatch.fails() == 1


def test_netwatch_does_not_reboot_soon_after_boot_or_reboot(netwatch):
    (netwatch.path / "uptime").write_text("600.00 10.00\n")  # 켜진 지 10분
    for _ in range(6):
        netwatch.run(FAKE_ROUTE=0)
    assert "reboot" not in netwatch.calls()
    # 오래 켜져 있었어도 이 장치가 1시간 안에 재부팅했다면 기다린다
    (netwatch.path / "uptime").write_text("5000.00 10.00\n")
    import time

    (netwatch.path / "last-reboot").write_text(f"{int(time.time()) - 600}\n")
    for _ in range(5):
        netwatch.run(FAKE_ROUTE=0)
    assert "reboot" not in netwatch.calls()
    assert "not rebooting yet" in netwatch.log()


def test_netwatch_metadata_check_only_after_it_answered_once(netwatch):
    # AWS 가 아닌 서버: 정보 주소가 처음부터 대답하지 않으면 경로만 본다
    netwatch.run(FAKE_META=0)
    assert netwatch.fails() == 0
    # 한 번 대답한 뒤 대답이 없으면 끊긴 것으로 본다(경로는 남아 있어도)
    netwatch.run(FAKE_META=1)
    netwatch.run(FAKE_META=0)
    assert netwatch.fails() == 1


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="관리자 권한이 필요한 등록 시험")
def test_netwatch_install_writes_cron(netwatch):
    netwatch.run("install")
    cron = (netwatch.path / "cron").read_text()
    assert re.search(r'^\*/2 \* \* \* \* root /bin/bash ".*/deploy/netwatch\.sh"', cron, re.M)


def test_install_and_update_register_netwatch():
    for name in ("install.sh", "update.sh"):
        assert 'deploy/netwatch.sh" install' in (DEPLOY / name).read_text(encoding="utf-8"), name
