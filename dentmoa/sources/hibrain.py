"""하이브레인넷 (hibrain.net) — 대학 교원 채용정보.

치과대학·치의학전문대학원 전임교원, 연구교수, 임상교수 공고가 올라온다.
"""

from __future__ import annotations

from .aggregators import AggregatorSource, Query

SEARCH_URL = "https://www.hibrain.net/recruitment/recruits"


class HibrainSource(AggregatorSource):
    key = "hibrain"
    label = "하이브레인넷"
    homepage = "https://www.hibrain.net"
    default_inst_type = None
    item_href = r"/recruitment/recruits/\d+"
    queries = [
        Query(SEARCH_URL, {"listType": "ING", "keyword": kw, "page": "{page}"})
        for kw in ("치과", "치의학", "구강")
    ]
    content_selectors = [".recruitDetail", ".recruit-detail", ".detail_cont", ".view_content", "#recruitContents", ".content"]
    inst_labels = ["대학명", "기관명", "학교명", "기관", "소속"]
