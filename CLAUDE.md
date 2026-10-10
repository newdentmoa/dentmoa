# 덴트모아 (dentmoa) — 개발 메모

개인용 치과의사 구인공고 알리미. 사용자는 소아치과 전문의이며 코딩을 하지 않는다.
사용자에게 보이는 모든 글(화면, 알림, 문서, 오류 메시지)은 **한국어**로 쓴다.

## 사용자와 일하는 방식 (사용자 요청, 2026-10-09)
- 사용자는 시키는 대로 그대로 따라 하며 진행한다. 설명은 쉽고 자세하게, 한 번에 한 단계씩.
- 매 단계가 끝나면 답의 마지막에 늘 다음을 붙인다:
  1) 남은 단계 목록 (끝난 것은 ✅)
  2) 다음에 사용자가 보낼 명령 (그대로 복사해 보낼 수 있는 한 줄)
  3) '나중에 해도 되는 개선 작업' 목록 (아래 '다음 세션에서 할 일'과 같은 내용) — 잊지 않도록 매번
- 남은 단계 (2026-10-10 기준): ✅ 서버 만들기(docs/1, Lightsail 서울 월 5달러, 고정 IP → https://3-39-124-154.sslip.io)
  → ✅ 설치(docs/2, 비밀번호·모어덴·덴트포토 연결 테스트 성공) → ✅ 텔레그램(docs/3, SH·JY 둘 다 테스트 메시지 받음)
  → 메일(docs/4, 보내는 계정은 dentmoa.team Gmail 권장) → 알림 조건(docs/5, SH·JY 각자)
  → (권장) AWS 요금 알림(docs/1 끝) + AWS MFA(보내는 Gmail 이 AWS 로그인 메일과 같으므로) → (선택) Claude 작업 환경의 환경 변수 비밀번호 지우기.
  AWS 크레딧이 가입 당일 0달러였음 → 다음 날 다시 확인, 그래도 0이면 결제 지원 문의 글(영문)을 같이 쓴다.
  (개선 작업 PR newdentmoa/dentmoa#3 과 두 사람 기능 PR 은 합쳤다.)
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
  db.py              SQLite (postings / runs / kv / person_postings). save_raw() 가 저장과 분류를 함께 한다
  settings_store.py  설정(kv 'settings': people(사람별 조건·알림·받는 곳) + 함께 쓰는 notify·collect),
                     계정정보(kv 'secrets', 환경변수 우선), 대시보드 비밀번호
  matching.py        match(posting, filters) -> MatchResult(ok, reasons)
  dedup.py           같은 공고 판별
  pipeline.py        collect() → digest() → reminders(), run_cycle()
  sources/           사이트별 수집기 (base.Source 상속, fetch(ctx) 가 RawPosting 을 yield)
    htmlutil.py      게시판 일반 읽기: find_list_items(날짜가 있는 줄 = 글 목록), extract_main_text, 날짜 읽기
    platforms.py     병원 공동 채용 사이트 (recruiter.co.kr 목록 JSON, 가톨릭중앙의료원, 건양대 채용 프로그램) — hospitals 가 주소를 보고 고른다
  notify/            텔레그램·메일 (send_digest, send_reminders)
  web/               Flask 대시보드 (people.py: 위쪽 SH|JY 단추 — 고른 사람은 세션에 기억)
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
- 실패 알림(pipeline._fresh_warnings, 사용자 요청 2026-10-10): 접속 실패·화면 구조 문제는 같은 출처가 연속 WARN_AFTER_FAILURES(3)번
  실패했을 때만, 계정 문제(로그인·계정 정보 없음)는 바로. 같은 출처는 하루 한 번. 놓친 글은 따라잡기로 메운다 — 출처 전체는
  make_context(마지막 성공 −2일, 36시간 넘으면 backfill_max_pages), 병원 게시판은 게시판마다 board_status 의 last_ok 로
  (hospitals._board_ctx, 처음 읽는 게시판은 FIRST_READ_DAYS=30일).
- 로그인 실패 뒤에는 같은 아이디·비밀번호로 23시간(LOGIN_RETRY_AFTER) 안에 다시 시도하지 않는다(계정 잠김 방지, kv 'login_failed' 에
  비밀번호가 아닌 지문만 저장). 계정 화면에서 아이디·비밀번호를 바꾸면 바로 다시 시도, 성공하면 기록을 지운다.
- 받는 사람 여럿 (사용자 요청 2026-10-10: 나=SH, 아내=JY). settings['people'] = [{key p1·p2…(DB 기록 키, 바뀌지 않음), name(별명,
  화면에서 바꿈), filters, alerts(telegram·email·send_empty·reminder_*·warnings), telegram_chat_id, email_to}], 최대 5명.
  처음부터 SH(p1)·JY(p2). 예전 한 사람용 설정·계정 화면의 채팅 ID·받는 주소·postings 의 processed/notified/reminded/starred 는
  p1 로 옮긴다(settings_store._from_legacy, db._migrate). 환경변수 TELEGRAM_CHAT_ID·EMAIL_TO 는 첫 사람 칸이 비었을 때만.
  사람별: 조건, 알림 방법, 받는 곳, 알림 판단·알림·마감 알림 기록과 관심(★)(person_postings). 함께: 알림 시각, 수집, 숨기기, 봇 토큰·보내는 메일.
  pipeline.digest/reminders 는 사람마다(settings_store.person_settings·person_secrets), 받는 사람이 둘 이상이면 제목에 이름
  (pipeline.who). 사이트 경고는 alerts.warnings 켠 사람만(기본 첫 사람만). 받는 곳이 없는 사람은 공고를 남겨 두었다가 연결하면 받는다.
  대시보드: 위쪽 SH|JY 로 보는 사람을 고르면 '○○ 조건에 맞는 공고'·관심(★)·알림 조건·미리보기가 그 사람 기준, 카드에 맞는 사람 이름 표시.
- 첫 비밀번호 설정은 6자리 설치 코드가 필요하다(DENTMOA_SETUP_CODE, install.sh 가 deploy/.env 에 만든다).
- 치과의사 공고 판단(classify.detect_dentist): ⓪ 제목이 연구직(Post-Doc·박사후연구원)이면 아님 ① 제목이 직원 직종(일반직·단시간근무자 포함)이면 아님 ② 보건소장은 치과의사 공고(분과 gp)
  ③ '모집분야·채용직종' 칸이 있으면 그 값으로 ④ 치과의사·치과 전문의 같은 말이 있으면 맞음 ⑤ 직원 직종(위생사·간호사·
  단시간근무자 등)이 적혀 있으면 교수·전문의 같은 자리 이름이 따로 있어야 맞음 ⑥ 그 밖에는 진료과 이름·치과+자리 이름.
  진료과 이름(소아치과 등)은 직원 공고의 근무 부서로도 나오므로 그것만으로는 치과의사 공고로 보지 않는다(직원 직종이 있을 때).
  '청년인턴·체험형 인턴'은 수련의 인턴이 아니다. 2026-10-10 실제 공고 48건(잡알리오·하이브레인넷·병원 게시판)으로 확인함.
  대학 전체 교원 공고에 늘 붙는 '의과대학, 치과대학 지원자는 …', '의학 및 치의학계열의 경우 …' 같은 대학 나열(COLLEGES_NOTE_RE)은
  본문에서 빼고 본다(CLASSIFIER_VERSION 5, 연세대 법학전문대학원 공고가 치과의사 공고로 잡히던 문제).
- 예전 미해결 분류 사례 4가지는 2026-10-10 모두 해결(CLASSIFIER_VERSION 6, 사용자 결정):
  '4주 대진' → 직위 봉직의·근무형태 대진만(본문 날짜 '11월 3일'을 '월 3일'(파트)로 읽던 문제 — PART_MONTHLY_RE 앞에 숫자·'개'가 오면 아님,
  기간이 정해진 대진(LOCUM_PERIOD_RE)이면 '월 2회' 도 파트로 보지 않음), 구직자에게 주는 조언·정보 글 → 글 종류 '기타'
  (ADVICE_RE: 조언·팁·체크리스트·주의할 점·[정보] 등, ADVICE_WEAK_RE: 필독·참고하세요·궁금합니다 등은 제목에 구인 낱말이 없을 때만),
  '서울구치소' → 기관 목록에 교정시설 4곳(서울구치소=경기 의왕시, 서울동부구치소=송파구, 서울남부구치소·교도소=구로구),
  '경기도의료원 이천병원' → 분원 항목. 도로 이름 '안양판교로'(의왕시)·'안양천로'(양천구)의 '안양' 은 지역이 아니다(regions.ROAD_AFTER_RE).

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
- 병원 게시판: watch 66곳(2026-10-10). recruiter.co.kr 32곳(공고명 검색 '치과','구강'), incruit 3곳(경희의료원·강동경희, 단국대치과는 브라우저),
  가톨릭중앙의료원 통합 채용 사이트 1곳(서울성모 항목 하나로 7개 병원 — 아래), 건양대 채용 프로그램 1곳(아래).
- 꺼 둔 게시판 새 주소 찾기(2026-10-10): 9곳 되살림 — recruiter.co.kr 로 옮긴 곳 7곳(경기도의료원 medical(6개 병원 공동, shared —
  제목 앞 '[파주병원]' 으로 '경기도의료원 파주병원' 같은 분원 항목을 찾음, hospitals._hints), 제주대병원 jejunuh, 국립암센터 ncc(치과의사
  전공의 공고가 올라옴), 고신대복음병원 kosinmed, 대구가톨릭대병원 dcmc, 원광대병원 wkuh, 경상국립대병원 gnuh(2026년 새로 엶)),
  건양대병원(www.kyuh.ac.kr/prog/recruitNotice/list.do — 서버 목록이지만 글 링크가 <button onclick="fn_search_view('RT…')"> 라서
  platforms.recruit_notice_items 로 따로 읽음, kyuh.recruiter.co.kr 은 2022년에 멈춤), 연세대 치과대학(faculty.yonsei.ac.kr 교원 초빙 —
  대학 전체 공고 본문 표에 '치과대학 / 통합치의학' 같은 자리가 섞여 나옴. title_filter 없이 치과대학 규칙으로 본문까지 읽는다).
  그대로 끈 곳(note 에 이유): 강북삼성(새 사이트 kbsmcrecruit.ninehire.site 는 자동 접속 확인 화면, 공고 안 보임), 충남대·세종충남대
  (새 채용 화면 cnuh.co.kr/prog/recruitNotice/01|02/ALL 은 표 모양이고 글이 2020년 1건뿐 — 잡알리오로 모임), 계명대동산(dsmc.or.kr 500),
  조선대·전북대 치과대학(새 주소 없음, 하이브레인넷), 국군수도병원(404, 이 작업 환경에서는 접속 자체가 안 됨 — 나라일터).
- 가톨릭중앙의료원(recruit.cmcnu.or.kr, 2026-10-10 확인): 예전 주소 /<병원>/application/appList.do 는 404, 사이트 암호화가 낡아
  파이썬 기본 설정으론 WRONG_SIGNATURE_TYPE(해외 차단 아님) → base.LEGACY_TLS_HOSTS(인증서 확인은 그대로, SECLEVEL=0).
  /cmc/index.do 의 '전체 기관 검색'(검색어 CP949)으로 '치과'·'구강'을 한 번씩 → platforms.cmc_items, hospitals._cmc.
  6곳 모두 치과가 있고(서울성모 13명 등) 소아치과 전문의는 없다. 합격자 안내 기록(2010~)에 치과의사 공고는 2018 의정부성모 1건뿐,
  나머지는 치위생직·치기공직·간호직(치과팀). 가톨릭 병원의 의사 계약직(진료전문의 등)은 하이브레인넷에 올라온다.
- 접속이 안 됐던 곳: 가톨릭중앙의료원 6곳 → 해결(위). 강원대치과병원·삼성창원 → 사용자 요청으로 끔(강원·창원 이직 계획 없음, 2026-10-10).
  조선대 www3.chosun.ac.kr 은 가끔 끊김.
  정부 사이트(잡알리오·나라일터)도 해외에서는 가끔 연결이 끊긴다 — PoliteSession 의 재시도로 넘어간다.
- 2026-10-09 미국(GCP 오하이오)에서 69곳(watch 64 + 출처 5) 접속 시험: 51곳 정상, 10곳 가끔 끊김(잡알리오·나라일터·아주대 등),
  8곳 3번 모두 실패(가톨릭 6곳·강원대치과는 TLS 오류 — 이 작업 환경의 중계 때문일 수도, 삼성창원은 연결 끊김).
  국내 대상 사이트와 github.com 은 IPv6 주소가 없다 → IPv6 전용 서버는 못 씀.
- 메모리 실측: 대시보드(serve) 약 40MB, 모어덴 수집(크로미움) 최고 약 520MB(PSS 합, 메모리 넉넉할 때), 덴트포토 약 50MB.
  512MB 서버 흉내(cgroup v1 memory.limit + 2GB 스왑, 2026-10-10): 앱 몫 250MB·150MB(2회) 모두 성공(43~56초, 메모리+스왑 최고
  약 390MB), 100MB 에서만 실패('목록을 찾지 못했습니다'). → 사용자가 Lightsail 512MB(월 5달러)로 결정. 느려지는 것은 상관없고
  놓치지만 않으면 된다는 기준. 실패 대비: 모어덴 목록을 못 받으면 같은 실행에서 한 번 더 열고, 마지막 성공 뒤 36시간
  (pipeline.CATCH_UP_AFTER)이 지났으면 max_pages 대신 backfill_max_pages(기본 10쪽)까지 읽어 따라잡는다.
  계속 실패하면 스냅샷으로 7달러(1GB)로 올린다(docs/1 끝).
- 호스팅: 서버(Lightsail 서울, 월 5달러). 2026-10-10 다시 확인: Lightsail '3개월 무료'는 없어지고 AWS Free Tier 크레딧(가입 100달러
  + 활동 최대 100달러, 만료 가입 후 12개월이라는 안내와 6개월이라는 안내가 섞여 있음)으로 바뀜. 무료 플랜(Free plan) 계정은 Lightsail 을
  못 쓰고 6개월 뒤 닫히므로 유료 플랜으로 업그레이드해야 함(크레딧은 이어짐). 사용자는 2026-10-10 dentmoa 용 계정을 무료 플랜으로 만들었음
  → docs/1 '1-1. 유료 플랜으로 바꾸기'. 그 뒤 '계정이 꼬인 것 같다'며 같은 메일로 다시 가입(유료 플랜) → 계정 dentmoa,
  크레딧 화면이 0달러(가입 당일). 하루 뒤 다시 보고, 그래도 0이면 결제 지원 문의(약관상 크레딧은 한 사람 한 계정만).
  GitHub Actions 예약 실행도 검토해 사용자에게 설명함 —
  대시보드 없음, 상태(DB) 저장을 따로 만들어야 함, 무료 2,000분/월이 빠듯(한 번에 15~20분), 약관상 애매.

## 다음 세션에서 할 일 (= 사용자에게 매번 보여 주는 '나중에 해도 되는 개선 작업')
1. (선택) 서버를 만든 뒤 서울 서버에서 꺼 둔 게시판 다시 접속해 보기: 국군수도병원(이 작업 환경에서는 접속 불가), 계명대동산(500),
   강북삼성 새 채용 사이트(ninehire). 열리면 watch 를 켠다.
- 끝난 것(2026-10-10): 분류 오류 고치기(CLASSIFIER_VERSION 4·5·6, 예전 미해결 사례 포함), 꺼 둔 게시판 새 주소 찾기(9곳 되살림),
  실패 경고는 연속 3번 실패할 때만 + 로그인 실패는 하루 한 번만 다시 시도 + 병원 게시판별 따라잡기,
  아내와 함께 쓰기(받는 사람 SH·JY — 위 '원칙'. 두 벌 설치는 수집 2배·메모리 2배라 하지 않음).
- 하지 않기로 한 것: 브라우저(모어덴) 메모리 줄이기 — 512MB 서버 시험에서 이미 성공했고, 따라잡기가 있어 이득이 거의 없다.
  모어덴 실패 경고가 며칠씩 계속될 때만 다시 검토(먼저 7달러 플랜 올리기가 더 쉽다).
- 강원 게시판(강원대 치과대학, 강릉아산, 원주세브란스)은 끄지 않고, 사용자가 알림 조건의 지역으로 거른다(docs/5 단계에서 안내).

## 병원 게시판 (data/institutions.json)
- 리서치로 모은 160개 기관(경기도의료원 분원 4곳·서울 이름 교정시설 4곳은 2026-10-10 추가)(치과대학병원·치과대학·장애인치과·대학병원·공공병원)의 이름/별칭/위치/채용 주소.
- `watch: true` 인 66곳을 hospitals 수집기가 읽는다. 읽는 방법은 주소로 정해진다:
  `*.recruiter.co.kr` → platforms.recruiter_items (화면 주소가 /career/... 로 바뀐 곳도 /app/jobnotice/list.json 은 그대로 동작),
  recruit.cmcnu.or.kr → platforms.cmc_items, 주소에 /prog/recruitNotice/ → platforms.recruit_notice_items(건양대),
  그 밖 → htmlutil.find_list_items (incruit 도 여기서 읽힘).
- 항목별 선택 값: `title_filter: "dental"` (의료원 전체 게시판에서 치과 관련 제목만), `shared: true` (여러 병원 공동 사이트 →
  기관 이름·위치를 붙이지 않고 분류기가 제목에서 찾음. 단 제목 앞 '[파주병원]' 을 붙인 '<항목 이름> 파주병원' 이 기관 목록에 따로 있으면
  그 항목을 붙인다), `fetch: "browser"` (자바스크립트 확인 화면 → Chromium).
- 같은 게시판·사이트를 여러 기관이 쓰면 한 곳만 watch (예: snudh.recruiter.co.kr 은 서울특별시장애인치과병원 항목,
  yuhs.recruiter.co.kr(세브란스 계열)은 연세대학교 치과대학병원 항목). note 에 '2026-10 확인:' 으로 이유를 적었다.
- 치과대학 항목은 대학 전체 교원 게시판인 경우가 많다 → 치과 관련 단어가 없는 글에는 기관 이름을 붙이지 않는다(hospitals._hints).
- 기관 목록을 다시 만들 때: 리서치 JSON → scratchpad 의 build 스크립트 방식(이름 정리, 별칭 자동 생성, 같은 게시판 공유 시 한 곳만 watch).
