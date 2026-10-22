"""Per-site fetch hints (config/fetch_hints.yml) and the allowed-domain loader (config/domains.yml + config/universities.yml)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class FetchHint:
    render: str = "auto"                  # always | auto | never
    wait_selector: Optional[str] = None
    settle_ms: int = 1000


class Hints:
    def __init__(self, rules: Optional[list] = None, default: Optional[FetchHint] = None) -> None:
        self.rules, self.default = rules or [], default or FetchHint()

    @classmethod
    def load(cls, path: Path) -> "Hints":
        if not Path(path).is_file():
            return cls()
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        rules = []
        for r in data.get("rules", []) or []:
            rules.append((str(r["host"]).lower(), str(r.get("path_prefix", "/")),
                          FetchHint(r.get("render", "auto"), r.get("wait_selector"), int(r.get("settle_ms", 1000)))))
        d = data.get("defaults") or {}
        return cls(rules, FetchHint(d.get("render", "auto"), d.get("wait_selector"), int(d.get("settle_ms", 1000))))

    def lookup(self, url: str) -> FetchHint:
        sp = urlsplit(url)
        host, path = (sp.hostname or "").lower(), sp.path or "/"
        for h, prefix, hint in self.rules:
            if host == h and path.startswith(prefix):
                return hint
        return self.default


# ---------------- allowed domains ----------------
_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$")
_NAME_KEYS = ("name", "university", "university_name", "english_name")


def norm_name(name: str) -> str:
    return re.sub(r"[\s\-_.,()（）]+", "", name or "").lower()


def _clean_domain(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if "://" in v:
        v = urlsplit(v).hostname or ""
    v = v.lstrip(".").removeprefix("www.")
    return v if _DOMAIN.match(v) else None


def _collect(value, out: list, key: str = "") -> None:
    if "email" in key.lower() or "url" in key.lower() or key.lower() in _NAME_KEYS or key.lower() in ("nameZh".lower(), "name_zh"):
        return
    if isinstance(value, str):
        d = _clean_domain(value)
        if d and d not in out:
            out.append(d)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, out, key)
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect(v, out, str(k))


def _entries(data):
    """Yield (name, value) from: {name: ...}, [{name: ..., domains: [...]}, ...] or {universities: ...}."""
    if isinstance(data, dict) and isinstance(data.get("universities"), (list, dict)):
        data = data["universities"]
    if isinstance(data, dict):
        for k, v in data.items():
            yield str(k), v
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = next((item[k] for k in _NAME_KEYS if isinstance(item.get(k), str)), None)
                if name:
                    yield name, {k: v for k, v in item.items() if k not in _NAME_KEYS}


def load_allowed_domains(config_dir: Path) -> dict:
    """-> {normalised english name: [domains]} merged from domains.yml and universities.yml (email domains excluded)."""
    out: dict = {}
    for fname in ("domains.yml", "universities.yml"):
        p = Path(config_dir) / fname
        if not p.is_file():
            continue
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for name, value in _entries(data):
            bucket = out.setdefault(norm_name(name), [])
            found: list = []
            _collect(value, found)
            for d in found:
                if d not in bucket:
                    bucket.append(d)
    return out
