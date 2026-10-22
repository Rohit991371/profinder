"""Static fast path (task 3.2): httpx with throttle + retry/back-off, charset-aware decoding, optional proxy."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from .basic import USER_AGENT, decode_bytes
from .gate import RetryPolicy, Throttle, classify_status, parse_retry_after

MAX_BYTES = 3_000_000
_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "utf8": "utf-8", "big5-hkscs": "big5"}


def client_kwargs(proxy_url: Optional[str], timeout: float = 30.0) -> dict:
    """Keyword arguments for httpx.Client. The proxy (from PROXY_URL) is passed through, never logged."""
    kw: dict = {
        "headers": {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        "follow_redirects": True, "timeout": timeout,
    }
    if proxy_url:
        kw["proxy"] = proxy_url
    return kw


def make_client(proxy_url: Optional[str] = None, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(**client_kwargs(proxy_url, timeout))


def decode_html(raw: bytes, content_type: str = "") -> tuple[str, str]:
    """BOM, then the declared charset (HTTP header, then <meta>), then utf-8 / gb18030 / big5 (GBK and GB2312 are gb18030)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8"
    declared: list[str] = []
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        declared.append(m.group(1))
    m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", raw[:4096], re.I)
    if m2:
        declared.append(m2.group(1).decode("ascii", "ignore"))
    for name in declared:
        enc = _ALIASES.get(name.lower(), name.lower())
        if enc in ("iso-8859-1", "latin-1", "ascii", "us-ascii"):      # servers often lie here: fall through to detection
            continue
        try:
            return raw.decode(enc), enc
        except (LookupError, UnicodeDecodeError):
            continue
    return decode_bytes(raw)


@dataclass
class StaticResult:
    kind: str                       # ok | blocked | challenge | gone | http_error | timeout | connect_error | not_html
    status: Optional[int]
    final_url: str
    html: str = ""
    encoding: str = ""
    attempts: int = 1


@dataclass
class RawResponse:
    status: Optional[int]
    url: str
    content: bytes
    content_type: str
    kind: str                       # ok | blocked | challenge | gone | http_error | timeout | connect_error
    attempts: int


class StaticFetcher:
    def __init__(self, client, throttle: Throttle, retry: Optional[RetryPolicy] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client, self.throttle, self.retry, self._sleep = client, throttle, retry or RetryPolicy(), sleep

    def _request(self, url: str) -> RawResponse:
        host = re.sub(r"^[a-z]+://", "", url).split("/", 1)[0].lower()
        kind, last_status = "connect_error", None
        for attempt in range(1, self.retry.max_attempts + 1):
            self.throttle.wait(host)
            retry_after = None
            try:
                r = self.client.get(url)
            except httpx.TimeoutException:
                kind, last_status = "timeout", None
            except httpx.HTTPError:
                kind, last_status = "connect_error", None
            else:
                cls = classify_status(r.status_code)
                ctype = r.headers.get("content-type", "") or ""
                if cls != "retry":
                    k = {"ok": "ok", "challenge": "challenge", "blocked": "blocked", "gone": "gone"}.get(cls, "http_error")
                    return RawResponse(r.status_code, str(r.url), r.content[:MAX_BYTES], ctype, k, attempt)
                kind, last_status = "http_error", r.status_code
                retry_after = parse_retry_after(r.headers.get("retry-after"))
            if attempt < self.retry.max_attempts:
                self._sleep(self.retry.delay_for(attempt, retry_after))
        return RawResponse(last_status, url, b"", "", kind, self.retry.max_attempts)

    def get(self, url: str) -> StaticResult:
        r = self._request(url)
        if r.kind != "ok":
            return StaticResult(r.kind, r.status, r.url, attempts=r.attempts)
        if r.content_type and "html" not in r.content_type.lower() and "xml" not in r.content_type.lower():
            return StaticResult("not_html", r.status, r.url, attempts=r.attempts)
        text, enc = decode_html(r.content, r.content_type)
        return StaticResult("ok", r.status, r.url, text, enc, r.attempts)

    def get_text(self, url: str) -> tuple[Optional[int], str, str]:
        """For robots.txt: (status, body, kind)."""
        r = self._request(url)
        body = decode_html(r.content, r.content_type)[0] if r.content else ""
        return r.status, body, r.kind
