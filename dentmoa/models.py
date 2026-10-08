"""데이터 모양 정의."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from .regions import Region


@dataclass
class RawPosting:
    """출처(사이트)에서 가져온 그대로의 공고."""

    source: str
    source_id: str
    url: str
    title: str
    body: str = ""
    posted_at: datetime | None = None
    author: str = ""
    region_hint: str = ""  # 사이트가 따로 보여주는 지역 칸
    institution_hint: str = ""  # 사이트가 따로 보여주는 병원 이름 칸
    extra: dict = field(default_factory=dict)


@dataclass
class Posting:
    """DB에 저장된 공고 (분류 결과 포함)."""

    id: int
    source: str
    source_id: str
    url: str
    title: str
    body: str
    author: str
    region_hint: str
    institution_hint: str
    posted_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    post_kind: str
    is_dentist: bool
    inst_type: str
    institution: str
    positions: list[str]
    specialties: list[str]
    specialty_hints: list[str]
    work_types: list[str]
    regions: list[Region]
    deadline_kind: str
    deadline: date | None
    summary: list[str]
    uncertain: list[str]
    evidence: dict
    dup_of: int | None
    starred: bool
    hidden: bool
    processed_at: datetime | None
    notified_at: datetime | None
    reminded_at: datetime | None
    extra: dict

    @property
    def region_labels(self) -> list[str]:
        return [r.label for r in self.regions]

    @property
    def effective_date(self) -> datetime:
        return self.posted_at or self.first_seen_at

    @classmethod
    def from_row(cls, row) -> "Posting":
        def dt(v):
            return datetime.fromisoformat(v) if v else None

        def js(v, default):
            if not v:
                return default
            try:
                return json.loads(v)
            except ValueError:
                return default

        return cls(
            id=row["id"],
            source=row["source"],
            source_id=row["source_id"],
            url=row["url"],
            title=row["title"],
            body=row["body"] or "",
            author=row["author"] or "",
            region_hint=row["region_hint"] or "",
            institution_hint=row["institution_hint"] or "",
            posted_at=dt(row["posted_at"]),
            first_seen_at=dt(row["first_seen_at"]),
            last_seen_at=dt(row["last_seen_at"]),
            post_kind=row["post_kind"] or "hiring",
            is_dentist=bool(row["is_dentist"]),
            inst_type=row["inst_type"] or "other",
            institution=row["institution"] or "",
            positions=js(row["positions"], []),
            specialties=js(row["specialties"], []),
            specialty_hints=js(row["specialty_hints"], []),
            work_types=js(row["work_types"], []),
            regions=[Region.from_json(r) for r in js(row["regions"], [])],
            deadline_kind=row["deadline_kind"] or "none",
            deadline=date.fromisoformat(row["deadline"]) if row["deadline"] else None,
            summary=js(row["summary"], []),
            uncertain=js(row["uncertain"], []),
            evidence=js(row["evidence"], {}),
            dup_of=row["dup_of"],
            starred=bool(row["starred"]),
            hidden=bool(row["hidden"]),
            processed_at=dt(row["processed_at"]),
            notified_at=dt(row["notified_at"]),
            reminded_at=dt(row["reminded_at"]),
            extra=js(row["extra"], {}),
        )
