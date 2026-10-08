from datetime import timedelta

import pytest

from dentmoa import config, db, pipeline, settings_store
from dentmoa.__main__ import main
from dentmoa.models import RawPosting
from dentmoa.sources.base import Source


class FakeSource(Source):
    key = "fake"
    label = "가짜 게시판"
    dentist_only = True

    def fetch(self, ctx):
        ctx.dump("fake_list.html", "<html><body>목록</body></html>")
        yield RawPosting(
            source=self.key,
            source_id="1",
            url="https://example.com/1",
            title="[서울 강남] 소아치과 전문의 원장님 모십니다",
            body="주 4.5일, 세후 1500\n마감: 채용시",
            posted_at=config.now() - timedelta(hours=2),
        )


@pytest.fixture()
def fake_source(monkeypatch):
    monkeypatch.setattr(pipeline, "all_sources", lambda: [FakeSource()])


def test_classify_output(capsys):
    code = main(["classify", "[서울 강남] 소아치과 전문의 원장님 모십니다", "주 4.5일, 세후 1500\n마감: 채용시"])
    out = capsys.readouterr().out
    assert code == 0
    for expected in ("글 종류", "구인", "소아치과", "봉직의 (페이닥터)", "서울 강남구", "채용시 마감", "3줄 요약", "근거"):
        assert expected in out


def test_classify_hospital_board(capsys):
    code = main([
        "classify", "서울대학교치과병원 소아치과 전임의 모집", "접수기간: 2026. 10. 1 ~ 2026. 10. 20",
        "--source-kind", "hospital_board", "--posted", "2026-10-01",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "치과대학병원·치과대학 — 서울대학교치과병원" in out
    assert "전임의·임상강사 (펠로우)" in out
    assert "2026-10-20" in out


def test_help_and_usage_errors(capsys):
    assert main(["--help"]) == 0
    assert "python -m dentmoa" in capsys.readouterr().out
    assert main(["없는명령"]) == 1
    assert "오류" in capsys.readouterr().err
    assert main(["classify"]) == 1
    assert "제목" in capsys.readouterr().err
    assert main([]) == 1


def test_sources_command(tmp_db, capsys):
    assert main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "moreden" in out and "dentphoto" in out


def test_probe_saves_debug_files(tmp_db, fake_source, capsys):
    assert main(["probe", "fake"]) == 0
    out = capsys.readouterr().out
    assert str(config.DEBUG_DIR) in out
    assert "fake_list.html" in out and "진단" in out
    assert (config.DEBUG_DIR / "fake_list.html").exists()
    assert main(["probe", "nope"]) == 1


def test_collect_notify_reclassify(tmp_db, fake_source, capsys):
    assert main(["collect", "--source", "fake"]) == 0
    assert "새 글 합계: 1건" in capsys.readouterr().out

    assert main(["notify", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "미리보기" in out and "소아치과 전문의 원장님 모십니다" in out
    assert db.unprocessed()  # 미리보기는 처리 완료로 표시하지 않음

    assert main(["reclassify", "--all"]) == 0
    assert "1건" in capsys.readouterr().out


def test_run_once_without_channels(tmp_db, fake_source, capsys):
    assert main(["run-once"]) == 0
    out = capsys.readouterr().out
    assert "✓ 가짜 게시판" in out
    assert "알림 채널 미설정" in out


def test_set_password(tmp_db, monkeypatch, capsys):
    answers = iter(["correct horse", "different"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert main(["set-password"]) == 1
    answers = iter(["correct horse", "correct horse"])
    assert main(["set-password"]) == 0
    assert settings_store.check_password("correct horse")
