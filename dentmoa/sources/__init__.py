"""공고 출처 목록."""

from __future__ import annotations

from .base import Source


def all_sources() -> list[Source]:
    from .alio import AlioSource
    from .dentphoto import DentphotoSource
    from .gojobs import GojobsSource
    from .hibrain import HibrainSource
    from .hospitals import HospitalBoardsSource
    from .moreden import MoredenSource

    return [MoredenSource(), DentphotoSource(), AlioSource(), HibrainSource(), GojobsSource(), HospitalBoardsSource()]


def get_source(key: str) -> Source:
    for s in all_sources():
        if s.key == key:
            return s
    raise KeyError(key)
