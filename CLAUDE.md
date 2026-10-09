# 덴트모아 (dentmoa) — 개발 메모

개인용 치과의사 구인공고 알리미. 사용자는 소아치과 전문의이며 코딩을 하지 않는다.
사용자에게 보이는 모든 글(화면, 알림, 문서, 오류 메시지)은 **한국어**로 쓴다.

## 사용자와 일하는 방식 (사용자 요청, 2026-10-09)
- 사용자는 시키는 대로 그대로 따라 하며 진행한다. 설명은 쉽고 자세하게, 한 번에 한 단계씩.
- 매 단계가 끝나면 답의 마지막에 늘 다음을 붙인다:
  1) 남은 단계 목록 (끝난 것은 ✅)
  2) 다음에 사용자가 보낼 명령 (그대로 복사해 보낼 수 있는 한 줄)
  3) '나중에 해도 되는 개선 작업' 목록 (아래 '다음 세션에서 할 일'과 같은 내용) — 잊지 않도록 매번
- 남은 단계 (2026-10-09 기준): PR 합치기 → 서버 만들기(docs/1) → 설치(docs/2) → 텔레그램(docs/3) → 메일(docs/4)
  → 사이트 계정·알림 조건(docs/5) → (선택) Claude 작업 환경의 환경 변수 비밀번호 지우기.
  저장소 기본 브랜치는 `claude/dentist-job-alert-app-xmcl6s` 이다(main 없음). 설치 명령은 기본 브랜치를 받는다.

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
    htmlutil.py      게시판 일반 읽기: find_list_items(날짜가 있는 줄 = 글 목록), extract_main_text, 날짜 읽기
    platforms.py     병원 공동 채용 사이트 (recruiter.co.kr 목록 JSON) — hospitals 가 주소를 보고 고른다
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
- 클라우드 개발 세션: 미리 깔린 Chromium(/opt/pw-browsers, chromium-1194)에 맞추려면 `pip install playwright==1.56.0`.
  모어덴·덴트포토 시험은 DENTMOA_DATA 를 임시 폴더로 두고 한다(로그인 쿠키·브라우저 저장소가 거기 남는다). 틀린 비밀번호로 여러 번 시도하지 말 것.
- 첫 비밀번호 설정은 6자리 설치 코드가 필요하다(DENTMOA_SETUP_CODE, install.sh 가 deploy/.env 에 만든다).
- 알려진 미해결 분류 사례: 근무 기간이 제목에만 있는 '4주 대진'의 파트 오판, 구직자 대상 조언 글, '서울구치소'(의왕시) 위치,
  '경기도의료원 이천병원'의 위치(기관 목록의 경기도의료원=수원으로 잡힘).

## 실제 사이트 확인 결과 (2026-10-09 세션)
각 수집기 파일 맨 위 설명에 확인한 화면 구조를 적어 두었다. tests/fixtures/sites 에 실제 화면을 줄인 HTML/JSON(전화번호·이메일·
개인 병원 정보는 지움)이 있고 tests/test_sites.py 가 그것으로 시험한다. 화면이 바뀌면 이 테스트부터 고친다.
- 잡알리오: 검색은 recruit.do 에 폼 POST(keyword=치과) + 치과병원 4곳 기관코드(org_name) 검색. 목록 링크 글자가 비어 있음. ✅ 실제 수집 확인
- 하이브레인넷: /recruitment/recruits?keyword=치과|치의 (본문까지 검색됨 → '투자유치과' 같은 글은 뺌). 일부 글은 회원 전용(제목만). ✅
- 나라일터: apmList.do 에 POST(searchKeyword=치과|구강). 본문은 숨겨진 textarea#content. ✅
- 덴트포토: 로그인은 member.dentphoto.com/login/login_check.php (dp_id, dp_password, login_url) → 성공하면 목록으로 보내는 스크립트.
  목록 board.dentphoto.com/recruit_dental/list.php (div.board_list > ul 한 줄 = 글 하나: 번호|분류(구인·양도)|'지역 | 제목'|작성자|날짜…,
  한 쪽 50건, 2쪽부터 ?gotopage=N). 글은 list2content.php → (스크립트) content.php (num 만으로는 안 열림). '양도' 글은 뺀다.
  ✅ 실제 계정으로 로그인·수집 확인 (2026-10-09 두 번째 세션)
- 모어덴: 로그인은 auth.deneer.co.kr (치과의사용 #communityIdInput — 직원용 마켓 칸과 헷갈리지 말 것). 목록은 JSON
  (public-api.moreden.co.kr/community/management/recruit/list?page=N, 'board' 20건). 목록 JSON 에 본문(content)까지 있어서
  글 화면(/article/<bid>)은 열지 않는다. 급여 0·10000(1만원)은 '협의'. 로그인은 브라우저 저장소에 남아 다음 실행에 재사용.
  ✅ 실제 계정으로 로그인·수집 확인 (2026-10-09 두 번째 세션). 같은 공고가 덴트포토에도 올라오면 dedup 이 묶는 것도 확인.
- 병원 게시판: watch 64곳. 그중 recruiter.co.kr 25곳(공고명 검색 '치과','구강'), incruit 2곳(경희의료원·강동경희), 브라우저 1곳(단국대치과).
- 이 세션에서 접속이 안 된 곳(해외에서 막힘/인증서 문제일 수 있음, 서울 서버에서는 될 수도 있음): 가톨릭중앙의료원 recruit.cmcnu.or.kr 6곳
  (TLS WRONG_SIGNATURE_TYPE), 강원대치과병원 gwnudh.or.kr(→ knudh.or.kr 로 넘어감, 인증서 체인 문제), 조선대 www3.chosun.ac.kr(가끔 끊김), 삼성창원.
  정부 사이트(잡알리오·나라일터)도 해외에서는 가끔 연결이 끊긴다 — PoliteSession 의 재시도로 넘어간다.
- 2026-10-09 미국(GCP 오하이오)에서 69곳(watch 64 + 출처 5) 접속 시험: 51곳 정상, 10곳 가끔 끊김(잡알리오·나라일터·아주대 등),
  8곳 3번 모두 실패(가톨릭 6곳·강원대치과는 TLS 오류 — 이 작업 환경의 중계 때문일 수도, 삼성창원은 연결 끊김).
  국내 대상 사이트와 github.com 은 IPv6 주소가 없다 → IPv6 전용 서버는 못 씀.
- 메모리 실측: 대시보드(serve) 약 40MB, 모어덴 수집(크로미움) 최고 약 520MB(PSS 합), 덴트포토 약 50MB.
  → Lightsail 1GB(월 7달러) 권장. 512MB 는 install.sh 의 2GB 스왑에 기대야 해서 느리고 실패할 수 있다.
- 호스팅: 기본 안내는 서버(Lightsail 서울, 월 7달러, 처음 3개월 무료). GitHub Actions 예약 실행도 검토해 사용자에게 설명함 —
  대시보드 없음, 상태(DB) 저장을 따로 만들어야 함, 무료 2,000분/월이 빠듯(한 번에 15~20분), 약관상 애매.

## 다음 세션에서 할 일
1. 위의 접속 안 된 곳을 서울 서버(또는 네트워크가 다른 세션)에서 `probe hospitals` 로 다시 확인.
2. 실제 데이터에서 본 분류 문제: 치과 기관의 직원(위생사·장애인 제한경쟁 단시간근무자) 공고를 치과의사 공고로 봄,
   '[광주보훈병원]' 글의 기관이 중앙보훈병원으로 잡힘, 보건소장 모집(의사·치과의사 가능)의 판단,
   도로 이름 속 구 이름(안양 '관악대로' → 서울 관악구가 함께 잡힘).
3. 꺼 둔 게시판(watch:false 의 note 에 '2026-10 확인: 끔 — 이유')의 새 주소 찾기: 강북삼성(recruit.kbsmc.co.kr 없어짐),
   충남대·세종충남대(자바스크립트 목록), 경기도의료원·국군수도병원(404) 등.

## 병원 게시판 (data/institutions.json)
- 리서치로 모은 152개 기관(치과대학병원·치과대학·장애인치과·대학병원·공공병원)의 이름/별칭/위치/채용 주소.
- `watch: true` 인 64곳을 hospitals 수집기가 읽는다. 읽는 방법은 주소로 정해진다:
  `*.recruiter.co.kr` → platforms.recruiter_items (화면 주소가 /career/... 로 바뀐 곳도 /app/jobnotice/list.json 은 그대로 동작),
  그 밖 → htmlutil.find_list_items (incruit 도 여기서 읽힘).
- 항목별 선택 값: `title_filter: "dental"` (의료원 전체 게시판에서 치과 관련 제목만), `shared: true` (여러 병원 공동 사이트 →
  기관 이름·위치를 붙이지 않고 분류기가 제목에서 찾음), `fetch: "browser"` (자바스크립트 확인 화면 → Chromium).
- 같은 게시판·사이트를 여러 기관이 쓰면 한 곳만 watch (예: snudh.recruiter.co.kr 은 서울특별시장애인치과병원 항목,
  yuhs.recruiter.co.kr(세브란스 계열)은 연세대학교 치과대학병원 항목). note 에 '2026-10 확인:' 으로 이유를 적었다.
- 치과대학 항목은 대학 전체 교원 게시판인 경우가 많다 → 치과 관련 단어가 없는 글에는 기관 이름을 붙이지 않는다(hospitals._hints).
- 기관 목록을 다시 만들 때: 리서치 JSON → scratchpad 의 build 스크립트 방식(이름 정리, 별칭 자동 생성, 같은 게시판 공유 시 한 곳만 watch).
