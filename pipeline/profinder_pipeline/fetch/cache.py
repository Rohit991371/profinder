"""Page cache (task 3.4). Disk: <md5(url)>.html + .json in inputs/cache/pages/ (the same files discovery already wrote, 30 days).
Raw HTML stays on disk (Iteration 4's CSS extractors read it); the database (ingest.page) gets the cleaned markdown + hashes."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

TTL_DAYS = 30


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()  # noqa: S324 - cache key only, not security


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324 - change detection only


@dataclass
class CachedPage:
    url: str
    final_url: str
    status: int
    html: str
    render_mode: str            # static | playwright (files written by discovery have no field = static)
    encoding: str
    content_hash: Optional[str]
    fetched_at: datetime


class PageCache:
    def __init__(self, cache_dir: Path, ttl_days: int = TTL_DAYS,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.dir, self.ttl, self._now = Path(cache_dir), timedelta(days=ttl_days), now

    def paths(self, url: str) -> tuple[Path, Path]:
        k = url_hash(url)
        return self.dir / f"{k}.html", self.dir / f"{k}.json"

    def read(self, url: str, allow_stale: bool = False) -> Optional[CachedPage]:
        h, m = self.paths(url)
        if not (h.is_file() and m.is_file()):
            return None
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(meta["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if not allow_stale and self._now() - fetched > self.ttl:
                return None
            return CachedPage(url, meta.get("final_url", url), int(meta.get("status", 200)), h.read_text(encoding="utf-8"),
                              meta.get("render_mode", "static"), meta.get("encoding", "utf-8"), meta.get("content_hash"), fetched)
        except Exception:
            return None

    def write(self, url: str, final_url: str, html: str, status: int, render_mode: str, encoding: str,
              content_hash: str) -> datetime:
        self.dir.mkdir(parents=True, exist_ok=True)
        h, m = self.paths(url)
        fetched = self._now()
        meta = {"url": url, "final_url": final_url, "status": status, "fetched_at": fetched.isoformat(timespec="seconds"),
                "render_mode": render_mode, "encoding": encoding, "content_hash": content_hash}
        for path, data in ((h, html), (m, json.dumps(meta, ensure_ascii=False))):
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, path)
        return fetched
