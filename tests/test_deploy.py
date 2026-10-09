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
SCRIPTS = ["install.sh", "update.sh", "backup.sh"]


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
