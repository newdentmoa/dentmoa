"""잡알리오 (job.alio.go.kr) — 공공기관 채용정보.

국립대학교 치과병원(서울대·경북대·부산대·전남대·전북대·강릉원주대 치과병원)과
국립중앙의료원, 보훈병원 같은 공공 병원은 이곳에 채용 공고를 올려야 한다.
"""

from __future__ import annotations

from .aggregators import AggregatorSource, Query

LIST_URL = "https://job.alio.go.kr/recruit.do"


class AlioSource(AggregatorSource):
    key = "alio"
    label = "잡알리오"
    homepage = LIST_URL
    default_inst_type = "public_hospital"
    item_href = r"recruitview\.do\?.*idx=\d+"
    queries = [
        # 기관 이름에 '치과병원'이 들어간 곳의 모든 공고 (제목에 '치과'가 없어도)
        Query(LIST_URL, {"pageNo": "{page}", "search_yn": "Y", "org_name": "치과병원", "order": "REG_DATE"}),
        # 제목에 '치과'가 들어간 공고 (보훈병원·의료원 치과 등)
        Query(LIST_URL, {"pageNo": "{page}", "search_yn": "Y", "title": "치과", "order": "REG_DATE"}),
        Query(LIST_URL, {"pageNo": "{page}", "search_yn": "Y", "title": "치의", "order": "REG_DATE"}),
    ]
    content_selectors = [".recruitView", ".recruit_view", ".view_con", ".tbl_view", "#contents", ".board_view"]
