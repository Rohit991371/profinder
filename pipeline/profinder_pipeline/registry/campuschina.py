from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .models import RegistryRecord
from .textnorm import has_cjk

_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
_KEY = re.compile(r"universities_art/index\.html\?key=([0-9A-Fa-f]+)")
_NAME = re.compile(r'class="f-18 t">\s*<a[^>]*>\s*<div class="o">\s*(?:<i[^>]*>\s*</i>\s*)?(.*?)\s*</div>\s*</a>', re.S)
_CITY = re.compile(r"<span>\s*City:\s*(.*?)</span>", re.S)
_SITE = re.compile(r"<span>\s*Website:\s*(.*?)</span>", re.S)
_STUD = re.compile(r"Number of Students:\s*(\d+)")
_BADGE = re.compile(r'class="s-(211|985)"')
_ALT = re.compile(r'alt="([^"]*)"')


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def parse_list_html(text: str) -> list[RegistryRecord]:
    """Parse saved CampusChina list markup (a whole saved page, or the <li> snippets from the harvest script)."""
    out: list[RegistryRecord] = []
    for block in _LI.findall(text):
        k, n = _KEY.search(block), _NAME.search(block)
        if not (k and n):
            continue
        name = _clean(n.group(1))
        if not name:
            continue
        province = city = None
        c = _CITY.search(block)
        if c:
            parts = [p.strip() for p in _clean(c.group(1)).split("--")]
            province = parts[0] or None
            city = parts[1] if len(parts) > 1 and parts[1] else None
        site = _SITE.search(block)
        website = _clean(site.group(1)) if site else None
        stud = _STUD.search(block)
        zh = next((html.unescape(a) for a in _ALT.findall(block) if has_cjk(a)), None)
        out.append(
            RegistryRecord(
                source="campuschina", external_id=k.group(1).upper(), name_en=name, name_zh=zh,
                province=province, city=city, website=website or None, country="CN",
                students=int(stud.group(1)) if stud else None, badges=tuple(_BADGE.findall(block)),
            )
        )
    return out


def parse_names_json(text: str) -> list[RegistryRecord]:
    """The JSON saved from DevTools holds [{key,name}] pairs; every 2nd item per key is a long description -> skipped."""
    out: list[RegistryRecord] = []
    seen: set[str] = set()
    for item in json.loads(text):
        key, name = str(item.get("key", "")).upper(), html.unescape(str(item.get("name", ""))).strip()
        if not key or key in seen or len(name) > 120:
            continue
        seen.add(key)
        out.append(RegistryRecord(source="campuschina", external_id=key, name_en=name, country="CN"))
    return out


def load_campuschina(paths: list[str | Path]) -> list[RegistryRecord]:
    """Files (.html/.htm/.json) or directories of them. HTML records win over name-only JSON records (same key)."""
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix.lower() in {".html", ".htm", ".json"})
        elif p.is_file():
            files.append(p)
    by_key: dict[str, RegistryRecord] = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        recs = parse_names_json(text) if f.suffix.lower() == ".json" else parse_list_html(text)
        for r in recs:
            old = by_key.get(r.external_id or "")
            if old is None or (old.website is None and r.website is not None):
                by_key[r.external_id or ""] = r
    return list(by_key.values())
