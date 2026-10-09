# 덴트모아 (dentmoa) — 개발 메모

개인용 치과의사 구인공고 알리미. 사용자는 소아치과 전문의이며 코딩을 하지 않는다.
사용자에게 보이는 모든 글(화면, 알림, 문서, 오류 메시지)은 **한국어**로 쓴다.

## 구조

```
dentmoa/
  taxonomy.py        분류 체계(기관 종류·직위·분과·근무형태)의 키와 한글 이름 — 키는 DB/설정에 저장되므로 바꾸지 말 것
  regions_data.py    행정구역(2026-10 기준, 인천 2026-07 개편 반영)과 별칭
  regions.py         글에서 지역 찾기(parse_regions), 설정 지역 선택과 비교(selection_matches)
  institutions.py    data/institutions.json 의 알려진 기관(이름→종류·위치)
  classify.py        규칙 기반 분류기 classify(title, body, posted=..., source_kind=..., ...) -> Classification
  deadline.py        마감일 찾기
  summarize.py       본문 3줄 요약(규칙)
  models.py          RawPosting(사이트에서 가져온 글), Posting(DB 행)
  db.py              SQLite (postings / runs / kv). save_raw() 가 저장과 분류를 함께 한다
  settings_store.py  설정(kv 'settings'), 계정정보(kv 'secrets', 환경변수 우선), 대시보드 비밀번호
  matching.py        match(posting, filters) -> MatchResult(ok, reasons)
  dedup.py           같은 공고 판별
  pipeline.py        collect() → digest() → reminders(), run_cycle()
  sources/           사이트별 수집기 (base.Source 상속, fetch(ctx) 가 RawPosting 을 yield)
  notify/            텔레그램·메일 (send_digest, send_reminders)
  web/               Flask 대시보드
  scheduler.py       APScheduler — 설정의 알림 시각마다 run_cycle()
  __main__.py        CLI: python -m dentmoa serve | run-once | collect | notify | probe | reclassify | set-password
```

## 원칙
- 분류는 AI 없이 규칙(정규식)으로. 규칙을 바꾸면 classify.CLASSIFIER_VERSION 을 올린다(시작할 때 자동 재분류).
- 사이트는 조용히 읽는다: 하루 3번, 요청 사이 3~8초 무작위 대기, 이미 본 글은 다시 열지 않음, 쿠키 재사용.
- 비밀번호·토큰은 코드/깃에 절대 넣지 않는다. 환경변수 또는 대시보드의 계정 화면(DB)에만.
- 테스트: `python -m pytest`. 사이트 수집기는 tests/fixtures 의 HTML로 테스트한다(네트워크 없이).
- 분류 회귀 테스트: tests/fixtures/classify_cases.json(98건), classify_cases_adv.json(279건, 일부러 까다롭게 만든 글).
  규칙을 고치면 둘 다 통과해야 한다. 분류기가 기본값으로 채운 항목(Classification.uncertain)은 matching 에서 걸러내지 않는다.
- 첫 비밀번호 설정은 6자리 설치 코드가 필요하다(DENTMOA_SETUP_CODE, install.sh 가 deploy/.env 에 만든다).
- 알려진 미해결 분류 사례: 근무 기간이 제목에만 있는 '4주 대진'의 파트 오판, 구직자 대상 조언 글, '서울구치소'(의왕시) 위치,
  '경기도의료원 이천병원'의 위치(기관 목록의 경기도의료원=수원으로 잡힘).

## 사이트 접속 확인이 아직 안 된 부분 (다음 세션에서 할 일)
이 프로젝트를 처음 만든 세션은 네트워크가 막혀 있어 실제 사이트를 열어보지 못했다.
수집기(sources/*.py)는 일반적인 게시판 구조를 가정하고 여러 방법을 시도하도록 만들었다.
네트워크가 열린 세션에서는:
1. `python -m dentmoa probe moreden` (dentphoto, alio, hibrain, hospitals) 로 실제 HTML 을 data/debug 에 저장
2. 저장된 HTML 을 보고 sources/*.py 의 선택자(selector)·URL 을 실제 구조에 맞게 고친다
3. 개인정보를 지운 HTML 일부를 tests/fixtures 에 넣고 테스트를 추가한다
계정 정보는 환경변수 MOREDEN_ID, MOREDEN_PW, DENTPHOTO_ID, DENTPHOTO_PW 로 받는다.

## 병원 게시판 (data/institutions.json)
- 리서치로 모은 150개 기관(치과대학병원·치과대학·장애인치과·대학병원·공공병원)의 이름/별칭/위치/채용 주소.
- `watch: true` 인 63곳은 hospitals 수집기가 직접 읽는다(서버 렌더링 게시판으로 추정되는 곳).
- `watch: false` 로 둔 곳: 채용 플랫폼(recruiter.co.kr, incruit, recruit.severance.healthcare, 대학 교원채용 시스템 등)
  을 쓰는 곳 — 자바스크립트 화면이라 일반 방법으로 못 읽는다. 특히 **연세대 치과대학병원(세브란스 채용 포털)**,
  **경희대치과병원(incruit)**, 서울시장애인치과병원(snudh.recruiter.co.kr) 은 중요하므로
  네트워크가 열린 세션에서 플랫폼별 수집기(예: recruiter.co.kr 공통 수집기)를 만드는 것이 다음 할 일.
- 기관 목록을 다시 만들 때: 리서치 JSON → scratchpad 의 build 스크립트 방식(이름 정리, 별칭 자동 생성, 같은 게시판 공유 시 한 곳만 watch).
