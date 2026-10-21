from __future__ import annotations

import hashlib
import json
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..fetch.basic import USER_AGENT, _throttle, decode_bytes   # Iteration 1 helpers; Iteration 3 replaces this with the full gate
from ..registry.textnorm import host_allowed, host_of
from .links import Link, clean_url, extract_links


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    links: list[Link] = field(default_factory=list)
    from_cache: bool = False


class PoliteFetcher:
    """Allow-listed hosts only, robots.txt per host (cached), per-host delay, page budget per university, 30-day disk cache.
    Same robots rules as fetch/basic.py (no/HTML robots.txt = no restriction; unreadable = not allowed)."""

    def __init__(self, client, cache_dir: Path, ttl_days: int = 30, offline: bool = False) -> None:
        self.client, self.cache_dir, self.ttl, self.offline = client, Path(cache_dir), timedelta(days=ttl_days), offline
        self._robots: dict[str, tuple[str, urllib.robotparser.RobotFileParser | None]] = {}
        self.begin([], 0)

    def begin(self, domains: list[str], budget: int) -> None:
        self.domains, self.budget, self.used, self.cache_hits, self.exhausted = list(domains), budget, 0, 0, False
        self.blocked: list[str] = []
        self.errors: list[tuple[str, str]] = []
        self._fails: dict[str, int] = {}          # consecutive refusals per host
        self._open: set[str] = set()              # hosts we stop contacting for this university

    # ---- robots ----
    def _load_robots(self, base: str, host: str):
        _throttle(host)
        try:
            r = self.client.get(base + "/robots.txt")
        except Exception:
            return ("unreadable", None)
        body, _ = decode_bytes(r.content)
        if r.status_code in (404, 410) or 400 <= r.status_code < 500 and r.status_code != 200:
            return ("none", None)
        if r.status_code == 200 and "<html" in body[:1000].lower():
            return ("none", None)
        if r.status_code == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(body.splitlines())
            return ("parsed", rp)
        return ("unreadable", None)

    def _robots_ok(self, url: str) -> bool | None:
        sp = urlsplit(url)
        base = f"{sp.scheme}://{sp.netloc}"
        if base not in self._robots:
            self._robots[base] = self._load_robots(base, sp.netloc)
        kind, rp = self._robots[base]
        if kind == "none":
            return True
        if kind == "parsed":
            return rp.can_fetch(USER_AGENT, url)
        return None

    # ---- cache ----
    def _paths(self, url: str) -> tuple[Path, Path]:
        k = hashlib.md5(url.encode()).hexdigest()  # noqa: S324 - cache key only
        return self.cache_dir / f"{k}.html", self.cache_dir / f"{k}.json"

    def _read_cache(self, url: str) -> Page | None:
        h, m = self._paths(url)
        if not (h.is_file() and m.is_file()):
            return None
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
            if datetime.now(timezone.utc) - datetime.fromisoformat(meta["fetched_at"]) > self.ttl:
                return None
            html = h.read_text(encoding="utf-8")
            return Page(url, meta.get("final_url", url), 200, html, extract_links(html, meta.get("final_url", url)), True)
        except Exception:
            return None

    def _write_cache(self, url: str, final: str, html: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        h, m = self._paths(url)
        h.write_text(html, encoding="utf-8")
        m.write_text(json.dumps({"url": url, "final_url": final, "status": 200, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")

    def _refused(self, host: str | None) -> None:
        """Three refusals in a row (403/412/429/5xx/connection error) = bot protection or an outage: be polite and stop for now."""
        self._fails[host] = self._fails.get(host, 0) + 1
        if self._fails[host] >= 3:
            self._open.add(host)

    # ---- main entry ----
    def get(self, url: str) -> Page | None:
        url = clean_url(url)
        if not host_allowed(host_of(url), self.domains):
            self.errors.append((url, "off_domain"))
            return None
        cached = self._read_cache(url)
        if cached:
            self.cache_hits += 1
            return cached
        if self.offline:
            return None
        host = host_of(url)
        if host in self._open:
            self.errors.append((url, "host_blocked"))
            return None
        if self.used >= self.budget:
            self.exhausted = True
            return None
        ok = self._robots_ok(url)
        if ok is False:
            self.blocked.append(url)
            return None
        if ok is None:
            self.errors.append((url, "robots_unreadable"))
            return None
        self.used += 1
        _throttle(urlsplit(url).netloc)
        try:
            r = self.client.get(url)
        except Exception as e:
            self._refused(host)
            self.errors.append((url, type(e).__name__))
            return None
        if r.status_code != 200:
            if r.status_code in (403, 412, 429) or r.status_code >= 500:
                self._refused(host)
            self.errors.append((url, f"http_{r.status_code}"))
            return None
        self._fails[host] = 0
        ctype = (r.headers.get("content-type") or "").lower()
        if ctype and "html" not in ctype:
            self.errors.append((url, "not_html"))
            return None
        final = str(r.url)
        if not host_allowed(host_of(final), self.domains):
            self.errors.append((url, "redirect_off_domain"))
            return None
        html, _ = decode_bytes(r.content[:2_000_000])
        self._write_cache(url, final, html)
        return Page(url, final, 200, html, extract_links(html, final))
