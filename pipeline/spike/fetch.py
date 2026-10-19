"""Task 0.2 — polite fetch: robots check, per-domain delay, encoding handling, saved-file fallback.

Usage (from pipeline/):  python -m spike.fetch [--offline]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .common import CACHE_DIR, INPUTS_DIR, LIST_URL, ensure_dirs

USER_AGENT = "ProFinderPipelineSpike/0.1"
DELAY_SECONDS = 2.5
_last_hit_by_host: dict[str, float] = {}


@dataclass
class FetchResult:
    url: str
    text: str
    source: str            # "live" or "saved_file"
    http_status: int | None
    encoding: str
    robots_note: str
    fetched_at: str


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """UTF-8 (BOM tolerated) first, then gb18030 (superset of GBK/GB2312), then big5."""
    for enc, label in (("utf-8-sig", "utf-8"), ("gb18030", "gb18030"), ("big5", "big5")):
        try:
            return raw.decode(enc), label
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _throttle(host: str) -> None:
    wait = DELAY_SECONDS - (time.monotonic() - _last_hit_by_host.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_hit_by_host[host] = time.monotonic()


def check_robots(client: httpx.Client, url: str) -> tuple[bool, str]:
    """-> (allowed, note). A missing robots.txt (404, or an HTML error page) means no restrictions."""
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    _throttle(parts.netloc)
    try:
        r = client.get(robots_url)
    except httpx.HTTPError as exc:
        return False, f"robots.txt fetch failed ({type(exc).__name__}); not allowed"
    body, _ = decode_bytes(r.content)
    if r.status_code in (404, 410):
        return True, f"no robots.txt (HTTP {r.status_code})"
    if r.status_code == 200 and "<html" in body[:1000].lower():
        return True, "no robots.txt (an HTML page was returned instead)"
    if r.status_code == 200:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.splitlines())
        return rp.can_fetch(USER_AGENT, url), "robots.txt parsed"
    if 400 <= r.status_code < 500:
        return True, f"robots.txt HTTP {r.status_code} (treated as none)"
    return False, f"robots.txt HTTP {r.status_code} (server error; not allowed)"


def fetch_text(url: str, fallback: Path | None = None, offline: bool = False) -> FetchResult:
    """Fetch live (robots + throttle); on any problem or --offline use the saved file."""
    ensure_dirs()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    note = "offline requested" if offline else ""
    if not offline:
        try:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30.0) as client:
                allowed, note = check_robots(client, url)
                if allowed:
                    _throttle(urlsplit(url).netloc)
                    r = client.get(url)
                    if r.status_code == 200:
                        text, enc = decode_bytes(r.content)
                        key = hashlib.md5(url.encode()).hexdigest()
                        (CACHE_DIR / f"{key}.html").write_text(text, encoding="utf-8")
                        (CACHE_DIR / f"{key}.json").write_text(
                            json.dumps({"url": url, "status": r.status_code, "encoding": enc, "fetched_at": now}), encoding="utf-8")
                        return FetchResult(url, text, "live", r.status_code, enc, note, now)
                    note += f"; page HTTP {r.status_code}"
                else:
                    note += "; blocked by robots"
        except httpx.HTTPError as exc:
            note = f"live fetch failed ({type(exc).__name__})"
    if fallback is None or not fallback.exists():
        raise RuntimeError(f"Could not fetch {url} ({note}) and no saved file at {fallback}")
    text, enc = decode_bytes(fallback.read_bytes())
    saved_at = datetime.fromtimestamp(fallback.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    return FetchResult(url, text, "saved_file", None, enc, note or "saved file used", saved_at)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="skip the network, use inputs/tsinghua_list.html")
    args = ap.parse_args()
    res = fetch_text(LIST_URL, INPUTS_DIR / "tsinghua_list.html", offline=args.offline)
    print(f"source={res.source} status={res.http_status} encoding={res.encoding} chars={len(res.text)}")
    print(f"robots: {res.robots_note}")
    print("Chinese readable:", "教职工名录" in res.text)


if __name__ == "__main__":
    main()
