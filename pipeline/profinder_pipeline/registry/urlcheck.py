from __future__ import annotations

import json
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

UA = "ProFinderPipeline/0.2 (registry liveness check)"


def load_checks(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _swap_scheme(url: str) -> str:
    return "http://" + url[8:] if url.startswith("https://") else "https://" + url[7:]


def _robots_blocked(client, url: str) -> bool:
    """True only when a real robots.txt forbids '/'. Any problem reading robots.txt counts as 'no restriction'."""
    try:
        rr = client.get(url.rstrip("/") + "/robots.txt", headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        if rr.status_code == 200 and "<html" not in rr.text[:300].lower():
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(rr.text.splitlines())
            return not rp.can_fetch(UA, "/")
    except Exception:
        pass
    return False


def check_urls(urls: list[str], cache_file: Path, client, delay: float = 1.5, force: bool = False, retry_failed: bool = False) -> dict[str, dict]:
    """One GET of the home page per official URL (+ one robots.txt read). Cached; failures are recorded, never raised.
    After a CONNECTION error (not after an HTTP status) the other scheme (http/https) is tried once."""
    checks = load_checks(cache_file)
    for url in urls:
        old = checks.get(url)
        if old is not None and not force and not (retry_failed and old.get("status") != 200):
            continue
        rec: dict = {"status": None, "final_url": None, "error": None, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for attempt in (url, _swap_scheme(url)):
            try:
                if _robots_blocked(client, attempt):
                    rec["error"] = "robots_disallow"
                    break
                r = client.get(attempt, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
                rec.update(status=r.status_code, final_url=str(r.url), error=None)
                break
            except Exception as e:  # network errors are data, not crashes
                rec["error"] = type(e).__name__
        checks[url] = rec
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(checks, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(delay)
    return checks