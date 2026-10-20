from __future__ import annotations

import json
from pathlib import Path

from .models import RegistryRecord
from .openalex import http_get_json
from .textnorm import has_cjk

BASE = "https://api.ror.org/v2/organizations"


def _short(i: str | None) -> str | None:
    return i.rstrip("/").rsplit("/", 1)[-1] if i else None


def parse_org(o: dict) -> RegistryRecord:
    names = o.get("names") or []
    display = next((n["value"] for n in names if "ror_display" in (n.get("types") or [])), None)
    if display is None:
        display = next((n["value"] for n in names if "label" in (n.get("types") or []) and n.get("lang") == "en"), None)
    values = [n["value"] for n in names if n.get("value")]
    zh = next((n["value"] for n in names if n.get("value") and (n.get("lang") == "zh" or has_cjk(n["value"]))), None)
    site = next((l["value"] for l in (o.get("links") or []) if l.get("type") == "website"), None)
    loc = ((o.get("locations") or [{}])[0] or {}).get("geonames_details") or {}
    return RegistryRecord(
        source="ror", external_id=_short(o.get("id")), name_en=display, name_zh=zh, aliases=tuple(values),
        province=loc.get("country_subdivision_name"), city=loc.get("name"), website=site,
        country=loc.get("country_code"), ror_id=_short(o.get("id")),
    )


def parse_response(data: dict) -> list[RegistryRecord]:
    return [parse_org(x) for x in (data.get("items") or [])]


def cache_path(cache_dir: Path, slug: str) -> Path:
    return cache_dir / "ror" / f"{slug}.json"


def fetch(name: str, slug: str, cache_dir: Path, client_id: str | None, client, force: bool = False) -> Path:
    p = cache_path(cache_dir, slug)
    if p.exists() and not force:
        return p
    headers = {"Client-Id": client_id} if client_id else None
    data = http_get_json(client, BASE, params={"query": name, "filter": "locations.geonames_details.country_code:CN"}, headers=headers)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
