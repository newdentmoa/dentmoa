"""명령줄 도구: python -m dentmoa <명령>

    serve        대시보드와 예약 작업 실행 (서버에서 계속 켜 두는 명령)
    run-once     지금 한 번 수집 → 알림 → 마감 알림
    collect      수집만
    notify       알림만 (--dry-run: 보내지 않고 목록만)
    remind       마감 임박 알림만
    probe        사이트 화면(HTML)을 data/debug 에 저장 (구조 진단용)
    reclassify   저장된 공고 다시 분류
    set-password 대시보드 비밀번호 정하기
    classify     제목·본문을 넣어 분류 결과 보기 (규칙 점검용)
    sources      출처(사이트) 목록
    test-notify  텔레그램·메일 테스트 메시지 보내기
"""

from __future__ import annotations

import argparse
import getpass
import logging
import re
import sys
import time
import unicodedata
from datetime import date, datetime

from . import config

PROG = "python -m dentmoa"

# ──────────────────────────── 한글 argparse ────────────────────────────

_ERRORS = [
    (re.compile(r"the following arguments are required: (.+)"), "꼭 넣어야 하는 값이 빠졌습니다: {0}"),
    (re.compile(r"argument (.+?): invalid choice: (.+?) \(choose from (.+)\)"), "{0}: {1} 은(는) 없는 값입니다 (가능한 값: {2})"),
    (re.compile(r"unrecognized arguments: (.+)"), "알 수 없는 옵션입니다: {0}"),
    (re.compile(r"argument (.+?): expected one argument"), "{0} 뒤에 값을 넣어 주세요"),
    (re.compile(r"argument (.+?): invalid int value: (.+)"), "{0}: 숫자가 아닙니다: {1}"),
]


class _Formatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, "사용법: " if prefix is None else prefix)


class _Parser(argparse.ArgumentParser):
    """도움말과 오류 메시지를 한글로 보여 주는 ArgumentParser. 잘못 쓰면 종료 코드 1."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", _Formatter)
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "넣을 값"
        self._optionals.title = "옵션"
        self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="이 도움말 보기")

    def error(self, message):
        for pattern, korean in _ERRORS:
            m = pattern.search(message)
            if m:
                message = korean.format(*m.groups())
                break
        self.print_usage(sys.stderr)
        self.exit(1, f"오류: {message}\n")


# ──────────────────────────── 출력 도우미 ────────────────────────────


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _print_collect(report) -> None:
    from .notify.format import warning_text

    if not report.sources:
        print("수집할 출처가 없습니다 (설정에서 모두 꺼져 있음).")
        return
    for s in report.sources:
        line = f"{'✓' if s.ok else '✗'} {s.label} ({s.key}): {s.message}"
        if not s.ok:
            line += f"\n    → {warning_text(s.kind)}"
        print(line)


def _print_titles(postings) -> None:
    from .taxonomy import short

    for p in postings:
        tags = " · ".join(short(k) for k in [p.inst_type, *p.specialties])
        region = ", ".join(p.region_labels) or "지역 미상"
        print(f"  - {p.title}  [{tags} · {region}]")


def _print_sent(sent: dict[str, str], skipped: str) -> None:
    if sent:
        for ch, result in sent.items():
            print(f"{ch}: {'보냄 ✅' if result == 'ok' else '실패 — ' + result}")
    elif skipped:
        print(f"보내지 않음: {skipped}")
        if skipped == "알림 채널 미설정":
            print("  → 대시보드의 계정·연결 화면에서 텔레그램이나 메일을 설정해 주세요.")


class _KstFormatter(logging.Formatter):
    """로그 시각을 한국 시간으로."""

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, config.TZ).strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def _failed(sent: dict[str, str]) -> bool:
    """채널을 시도했는데 하나도 성공하지 못했는지."""
    return bool(sent) and not any(v == "ok" for v in sent.values())


def _print_digest(rep, *, dry_run: bool = False) -> None:
    """사람마다: 판단한 새 공고 수, 조건에 맞는 공고, 보낸 결과."""
    for d in rep.people:
        head = f"[{d.name}] " if len(rep.people) > 1 else ""
        print(f"{head}알림을 판단할 새 공고 {d.considered}건 중 조건에 맞는 공고 {len(d.matched)}건"
              + (" (미리보기 — 보내지 않음)" if dry_run else ""))
        _print_titles(d.matched)
        if not dry_run:
            _print_sent(d.sent, d.skipped_reason)


# ──────────────────────────── 명령 ────────────────────────────


def cmd_serve(args) -> int:
    import waitress

    from . import db, scheduler, settings_store

    handler = logging.StreamHandler()
    handler.setFormatter(_KstFormatter("[%(asctime)s] %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    config.ensure_dirs()
    settings_store.init_password_from_env()
    n = db.reclassify(only_outdated=True)
    if n:
        print(f"분류 규칙이 바뀌어 공고 {n}건을 다시 분류했습니다.")
    from . import web  # 웹 화면은 serve 할 때만 불러온다

    app = web.create_app()
    if not settings_store.has_password():
        print("안내: 대시보드 비밀번호가 아직 없습니다. 처음 화면에서 설치 코드 "
              f"{settings_store.setup_code()} 를 넣고 비밀번호를 정해 주세요. "
              "('python -m dentmoa set-password' 로 정해도 됩니다)")
    scheduler.start()
    print(f"덴트모아 대시보드: http://{args.host}:{args.port}" + (f"  ({config.BASE_URL})" if config.BASE_URL else ""))
    print("멈추려면 Ctrl+C")
    try:
        waitress.serve(app, host=args.host, port=args.port, threads=8)
    except KeyboardInterrupt:
        print("\n서버를 멈췄습니다.")
    finally:
        scheduler.shutdown()
    return 0


def cmd_run_once(args) -> int:
    from . import pipeline

    config.ensure_dirs()
    result = pipeline.run_cycle()
    if "skipped" in result:
        print(f"건너뜀: {result['skipped']}")
        return 1
    col, dig = result["collect"], result["digest"]
    print("[수집]")
    _print_collect(col)
    print("\n[알림]")
    _print_digest(dig)
    print(f"\n[마감 알림] {result['reminders']}건")
    print(f"\n요약: {result['message']}")
    return 1 if col.warnings or dig.failed else 0


def cmd_collect(args) -> int:
    from . import pipeline, settings_store

    config.ensure_dirs()
    report = pipeline.collect(args.source or None, save_debug=args.debug)
    if args.source and not report.sources:
        print(f"그런 출처가 없습니다: {', '.join(args.source)} (가능한 값: {', '.join(settings_store.SOURCE_KEYS)})",
              file=sys.stderr)
        return 1
    _print_collect(report)
    print(f"새 글 합계: {report.new_count}건")
    if args.debug:
        print(f"받은 화면(HTML)은 {config.DEBUG_DIR} 에 저장했습니다.")
    return 1 if report.warnings else 0


def cmd_notify(args) -> int:
    from . import pipeline

    config.ensure_dirs()
    rep = pipeline.digest(dry_run=args.dry_run)
    _print_digest(rep, dry_run=args.dry_run)
    return 1 if rep.failed else 0


def cmd_remind(args) -> int:
    from . import pipeline

    config.ensure_dirs()
    n = pipeline.reminders()
    print(f"마감 임박 알림 {n}건을 보냈습니다." if n else "보낸 마감 임박 알림이 없습니다 (대상이 없거나 꺼져 있거나 전송 실패).")
    return 0


def cmd_probe(args) -> int:
    from . import pipeline, settings_store

    config.ensure_dirs()
    print(f"'{args.key}' 사이트에서 받은 화면(HTML)을 {config.DEBUG_DIR} 에 저장합니다.")
    print("사이트 화면 구조를 진단하고 수집 프로그램을 고칠 때 쓰는 명령입니다. (가져온 공고는 평소처럼 저장됩니다)\n")
    started = time.time()
    report = pipeline.collect([args.key], save_debug=True)
    if not report.sources:
        print(f"그런 출처가 없습니다: {args.key} (가능한 값: {', '.join(settings_store.SOURCE_KEYS)})", file=sys.stderr)
        return 1
    _print_collect(report)
    files = sorted(
        p for p in config.DEBUG_DIR.glob("*") if p.is_file() and p.stat().st_mtime >= started - 1
    ) if config.DEBUG_DIR.exists() else []
    print(f"\n저장된 파일 {len(files)}개 — 위치: {config.DEBUG_DIR}")
    for p in files[:40]:
        print(f"  {p.name}")
    return 0 if report.sources[0].ok else 1


def cmd_reclassify(args) -> int:
    from . import db

    config.ensure_dirs()
    n = db.reclassify(only_outdated=not args.all)
    print(f"공고 {n}건을 다시 분류했습니다.")
    return 0


def cmd_set_password(args) -> int:
    from . import settings_store

    config.ensure_dirs()
    pw1 = getpass.getpass("새 대시보드 비밀번호 (8자 이상): ")
    pw2 = getpass.getpass("한 번 더 입력: ")
    if pw1 != pw2:
        print("두 비밀번호가 서로 다릅니다. 다시 해 주세요.", file=sys.stderr)
        return 1
    try:
        settings_store.set_password(pw1)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("대시보드 비밀번호를 저장했습니다.")
    return 0


_EVIDENCE_LABELS = {
    "post_kind": "글 종류",
    "is_dentist": "치과의사 공고",
    "inst_types": "기관 종류",
    "positions": "직위",
    "specialties": "분과",
    "specialty_hints": "참고 분야",
    "specialty_ignored": "무시한 분과",
    "work_types": "근무 형태",
    "regions": "지역",
}


def classification_text(c, today: date) -> str:
    """분류 결과를 읽기 쉬운 한글로."""
    from .notify.format import WEEKDAYS, dday
    from .taxonomy import INST_TYPES, POSITIONS, POST_KINDS, SPECIALTIES, WORK_TYPES

    def names(keys, table):
        return ", ".join(table.get(k, k) for k in keys) or "없음"

    def guess(dim):
        return "  (단서 없음 → 기본값)" if dim in c.uncertain else ""

    if c.deadline_kind == "date" and c.deadline:
        d = c.deadline
        deadline = f"{d.isoformat()}({WEEKDAYS[d.weekday()]}) · {dday(d, today)}"
    elif c.deadline_kind == "until_filled":
        deadline = "채용시 마감"
    else:
        deadline = "정보 없음"

    inst = INST_TYPES.get(c.inst_type, c.inst_type) + (f" — {c.institution}" if c.institution else "")
    rows = [
        ("글 종류", POST_KINDS.get(c.post_kind, c.post_kind)),
        ("치과의사 공고", "예" if c.is_dentist else "아니오"),
        ("기관 종류", inst + guess("inst_types")),
        ("직위", names(c.positions, POSITIONS) + guess("positions")),
        ("분과", names(c.specialties, SPECIALTIES)),
        ("참고 분야", names(c.specialty_hints, SPECIALTIES)),
        ("근무 형태", names(c.work_types, WORK_TYPES) + guess("work_types")),
        ("지역", ", ".join(r.label for r in c.regions) or "지역 미상"),
        ("마감", deadline),
    ]
    w = max(_width(k) for k, _ in rows) + 2
    lines = [f"{_pad(k, w)}{v}" for k, v in rows]
    lines.append("")
    lines.append("3줄 요약")
    lines += [f"  • {s}" for s in c.summary] or ["  (없음)"]
    lines.append("")
    lines.append("근거")
    if c.evidence:
        for key, items in c.evidence.items():
            label = _EVIDENCE_LABELS.get(key, key)
            lines.append(f"  {_pad(label, w)}{' / '.join(items) if items else '-'}")
    else:
        lines.append("  (없음)")
    return "\n".join(lines)


def cmd_classify(args) -> int:
    from .classify import classify

    body = sys.stdin.read() if args.body == "-" else (args.body or "")
    try:
        posted = date.fromisoformat(args.posted) if args.posted else config.now().date()
    except ValueError:
        print("--posted 는 2026-10-08 처럼 넣어 주세요.", file=sys.stderr)
        return 1
    c = classify(
        args.title,
        body,
        posted=posted,
        source_kind=args.source_kind,
        dentist_only=args.dentist_only,
        region_hint=args.region_hint,
        institution_hint=args.institution_hint,
    )
    print(classification_text(c, config.now().date()))
    return 0


def cmd_sources(args) -> int:
    from . import settings_store
    from .notify.format import FALLBACK_SOURCE_LABELS

    problem = None
    try:
        from .sources import all_sources

        rows = [(s.key, s.label, s.homepage, s.requires_login) for s in all_sources()]
    except Exception as e:  # 수집기 파일이 아직 없을 때도 목록은 보여 준다
        problem = e
        rows = [(k, FALLBACK_SOURCE_LABELS.get(k, k), "", False) for k in settings_store.SOURCE_KEYS]
    try:
        enabled = settings_store.load()["collect"]["sources"]
    except Exception:
        enabled = {}
    for key, label, home, login in rows:
        state = "켜짐" if enabled.get(key, True) else "꺼짐"
        extra = "  로그인 필요" if login else ""
        print(f"{_pad(key, 11)}{_pad(label, 18)}{state}{extra}" + (f"  {home}" if home else ""))
    if problem:
        print(f"\n(수집기 파일을 불러오지 못했습니다: {problem.__class__.__name__}: {problem})")
    return 0


def cmd_test_notify(args) -> int:
    from . import notify, settings_store
    from .notify import mailer, telegram

    sec = settings_store.secrets()
    ok = True
    tried = False
    for ch, configured in ((notify.TELEGRAM, telegram.is_configured(sec)), (notify.EMAIL, mailer.is_configured(sec))):
        if not configured:
            print(f"{ch}: 설정 안 됨 (건너뜀)")
            continue
        tried = True
        try:
            notify.send_test(ch, sec)
            print(f"{ch}: 보냄 ✅")
        except notify.NotifyError as e:
            ok = False
            print(f"{ch}: 실패 — {e}")
    if not tried:
        print("텔레그램과 메일이 모두 설정되지 않았습니다. 대시보드의 계정·연결 화면에서 입력해 주세요.")
    return 0 if ok and tried else 1


# ──────────────────────────── 파서 ────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROG,
        description="덴트모아 — 치과의사 구인공고 알리미 (개인용)",
        epilog=(
            "예:\n"
            "  python -m dentmoa serve               대시보드 + 예약 작업 실행\n"
            "  python -m dentmoa run-once            지금 한 번 수집하고 알림 보내기\n"
            "  python -m dentmoa probe moreden       모어덴 화면을 저장해서 구조 진단\n"
            '  python -m dentmoa classify "제목" "본문"   분류 결과 확인'
        ),
    )
    sub = parser.add_subparsers(dest="command", title="명령", metavar="<명령>")

    p = sub.add_parser("serve", help="대시보드와 예약 작업 실행", description="대시보드(웹)를 열고, 설정한 시각마다 수집·알림을 실행합니다.")
    p.add_argument("--host", default=config.HOST, help=f"접속 받을 주소 (기본 {config.HOST})")
    p.add_argument("--port", type=int, default=config.PORT, help=f"포트 번호 (기본 {config.PORT})")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("run-once", help="지금 한 번 수집 → 알림 → 마감 알림", description="예약 시각을 기다리지 않고 지금 한 번 전체 과정을 실행합니다.")
    p.set_defaults(func=cmd_run_once)

    p = sub.add_parser("collect", help="공고 수집만 하기", description="사이트에서 공고를 가져와 저장만 합니다 (알림은 보내지 않음).")
    p.add_argument("--source", nargs="+", metavar="출처", help="이 출처만 수집 (예: moreden dentphoto)")
    p.add_argument("--debug", action="store_true", help="받은 화면(HTML)을 data/debug 에 저장")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("notify", help="모인 새 공고 알림 보내기", description="아직 알리지 않은 공고 중 조건에 맞는 것을 텔레그램·메일로 보냅니다.")
    p.add_argument("--dry-run", action="store_true", help="보내지 않고 어떤 공고가 갈지 목록만 보기")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("remind", help="마감 임박 알림 보내기")
    p.set_defaults(func=cmd_remind)

    p = sub.add_parser(
        "probe",
        help="사이트 화면을 저장해 구조 진단",
        description="사이트에서 받은 화면(HTML)을 data/debug 에 저장합니다. 사이트 구조가 바뀌어 수집이 안 될 때 원인을 찾는 데 씁니다.",
    )
    p.add_argument("key", metavar="출처", help="출처 이름 (moreden, dentphoto, alio, hibrain, hospitals)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("reclassify", help="저장된 공고 다시 분류", description="분류 규칙이 바뀐 뒤 저장된 공고를 다시 분류합니다.")
    p.add_argument("--all", action="store_true", help="규칙이 바뀌지 않은 공고까지 모두 다시 분류")
    p.set_defaults(func=cmd_reclassify)

    p = sub.add_parser("set-password", help="대시보드 비밀번호 정하기")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser(
        "classify",
        help="제목·본문의 분류 결과 보기 (규칙 점검용)",
        description="제목과 본문을 넣으면 어떻게 분류되는지와 그 근거를 보여 줍니다. 본문 자리에 - 를 넣으면 붙여넣은 글을 읽습니다.",
    )
    p.add_argument("title", metavar="제목", help="공고 제목")
    p.add_argument("body", metavar="본문", nargs="?", default="", help="공고 본문 (생략 가능, - 이면 붙여넣은 글을 읽음)")
    p.add_argument("--source-kind", choices=["local_board", "hospital_board", "aggregator"], default="local_board",
                   help="출처 종류: local_board(모어덴·덴트포토, 기본) / hospital_board(병원 게시판) / aggregator(잡알리오 등)")
    p.add_argument("--dentist-only", action="store_true", help="치과의사 전용 게시판의 글로 보기")
    p.add_argument("--region-hint", default="", metavar="지역", help="사이트의 지역 칸 내용")
    p.add_argument("--institution-hint", default="", metavar="기관", help="사이트의 병원 이름 칸 내용")
    p.add_argument("--posted", default="", metavar="날짜", help="게시일 (예: 2026-10-08, 마감일 계산 기준)")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("sources", help="출처(사이트) 목록")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("test-notify", help="텔레그램·메일 테스트 메시지 보내기")
    p.set_defaults(func=cmd_test_notify)
    return parser


def _utf8_console() -> None:
    """윈도우 콘솔에서도 한글이 깨지지 않게."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # 도움말(0) 또는 잘못된 사용(1)
        return e.code if isinstance(e.code, int) else 1
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n중단했습니다.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"오류: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
