"""하이브레인넷 (hibrain.net) — 대학 교원 채용정보.

치과대학·치의학전문대학원 전임교원, 기금·연구교수, 일부 대학병원 임상교수 공고가 올라온다.
서버 쪽 검색어 기능이 확인되지 않아, 진행 중 공고를 최신순으로 넘기면서
제목·기관에 치과 관련 단어가 있거나 치과대학이 있는 대학의 교원 초빙 글만 연다.
"""

from __future__ import annotations

import re

from .aggregators import AggregatorSource, Query
from .htmlutil import ListItem

LIST_URL = "https://www.hibrain.net/recruitment/recruits"
DENTAL_RE = re.compile(r"치과|치의|구강|dental|dentistry", re.I)
# 치과대학(치의학전문대학원)이 있는 대학
DENTAL_UNIVS = re.compile(r"서울대|연세대|경희대|단국대|조선대|원광대|전남대|전북대|부산대|경북대|강원대|강릉원주대")
FACULTY_RE = re.compile(r"교원|교수|초빙")


class HibrainSource(AggregatorSource):
    key = "hibrain"
    label = "하이브레인넷"
    homepage = "https://www.hibrain.net"
    default_inst_type = None
    item_href = r"/recruitment/(?:categories/[^\s]*/)?recruits/\d+"
    queries = [Query(LIST_URL, {"listType": "ING", "pagesize": "50", "sortType": "SORTDTM", "page": "{page}"})]
    pages_factor = 2
    content_selectors = [".recruitDetail", ".recruit-detail", ".detail_cont", ".view_content", "#recruitContents", ".content"]
    inst_labels = ["대학명", "기관명", "학교명", "기관", "소속"]

    def wanted(self, it: ListItem) -> bool:
        text = f"{it.title} {it.row_text}"
        return bool(DENTAL_RE.search(text) or (DENTAL_UNIVS.search(text) and FACULTY_RE.search(text)))
