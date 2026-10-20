from __future__ import annotations

import json
from pathlib import Path

from .models import RegistryRecord
from .textnorm import has_cjk

BASE = "https://api.openalex.org/institutions"


def _short(i: str | None) -> str | None:
    return i.rstrip("/").rsplit("/", 1)[-1] if i else None


def parse_institution(o: dict) -> RegistryRecord:
    geo = o.get("geo") or {}
    alts = [a for a in (o.get("display_name_alternatives") or []) if a]
    zh = next((a for a in alts if has_cjk(a)), None)
    return RegistryRecord(
        source="openalex", external_id=_short(o.get("id")), name_en=o.get("display_name"), name_zh=zh,
        aliases=tuple(alts), province=geo.get("region"), city=geo.get("city"),
        website=o.get("homepage_url") or None, country=(o.get("country_code") or geo.get("country_code")),
        ror_id=_short(o.get("ror")),
    )


def parse_response(data: dict) -> list[RegistryRecord]:
    return [parse_institution(x) for x in (data.get("results") or [])]


def cache_path(cache_dir: Path, slug: str) -> Path:
    return cache_dir / "openalex" / f"{slug}.json"


def fetch(name: str, slug: str, cache_dir: Path, api_key: str | None, client, force: bool = False) -> Path:
    """Search institutions by name (CN only). Raw JSON is cached; the key is never logged or stored."""
    p = cache_path(cache_dir, slug)
    if p.exists() and not force:
        return p
    params = {"search": name, "filter": "country_code:CN", "per-page": "8"}
    if api_key:
        params["api_key"] = api_key
    data = http_get_json(client, BASE, params=params)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def http_get_json(client, url: str, params: dict | None = None, headers: dict | None = None, tries: int = 4) -> dict:
    import time

    delay = 3.0
    for attempt in range(tries):
        r = client.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            ra = r.headers.get("retry-after")
            time.sleep(float(ra) if ra and ra.isdigit() else delay)
            delay *= 2
            continue
        raise RuntimeError(f"HTTP {r.status_code} from {url.split('?')[0]}")
    raise RuntimeError("unreachable")
