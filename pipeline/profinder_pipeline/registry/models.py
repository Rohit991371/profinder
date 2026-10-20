from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryRecord:
    """One university as described by ONE registry (typed candidate with a source tag)."""

    source: str                      # moe | ror | openalex | campuschina
    external_id: str | None = None   # MOE code / ROR id / OpenAlex id / CampusChina key
    name_en: str | None = None
    name_zh: str | None = None
    aliases: tuple[str, ...] = ()
    province: str | None = None      # English (MOE province is mapped from Chinese)
    city: str | None = None          # English
    province_zh: str | None = None
    city_zh: str | None = None
    website: str | None = None
    country: str | None = None       # ISO code when the registry gives one
    ror_id: str | None = None        # OpenAlex records carry the ROR id
    students: int | None = None
    badges: tuple[str, ...] = ()
    note: str | None = None          # MOE level / remark


SOURCES = ("moe", "ror", "openalex", "campuschina")
