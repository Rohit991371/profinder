"""Fetch engine (tasks 3.1-3.4): one call, get(url), runs
   allow-list -> cache -> robots/budget gate -> static fetch -> (browser when refused / JS shell / hinted) -> clean -> cache.
Cache hits cost nothing (no robots check, no budget, no network). Every outcome is a FetchedPage; nothing raises."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from bs4 import BeautifulSoup

from ..discovery.links import clean_url
from ..registry.textnorm import host_of
from .cache import PageCache, md5_text, url_hash
from .clean import html_to_markdown
from .gate import Gate, interpret_robots
from .hints import Hints

CHALLENGE_MARKERS = ("just a moment", "access denied", "checking your browser", "enable javascript and cookies",
                     "验证码", "安全验证", "访问验证", "人机验证", "请开启javascript", "请启用javascript")
AJAX_MARKERS = ("$.ajax", ".ajax(", "xmlhttprequest", "fetch(", "axios", "$.post(", "$.get(")
_EMPTY_BOX = re.compile(r"""<div[^>]*class=["'][^"']*(?:list|con|content|box)[^"']*["'][^>]*>\s*</div>""", re.I)


def looks_like_challenge(html: str) -> bool:
    """A small page that only asks the visitor to verify / enable JavaScript. (Large pages are never challenges.)"""
    if len(html) > 20_000:
        return False
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    text = soup.get_text(" ", strip=True).lower()
    return len(text) < 600 and any(m in text for m in CHALLENGE_MARKERS)


def looks_like_js_shell(html: str) -> bool:
    """Generic hint that the content is filled in by JavaScript: an AJAX call in the page and (little text or empty list boxes)."""
    low = html.lower()
    if not any(m in low for m in AJAX_MARKERS):
        return False
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    return len(soup.get_text(" ", strip=True)) < 1500 or bool(_EMPTY_BOX.search(html))


@dataclass
class FetchedPage:
    url: str
    final_url: str = ""
    status: Optional[int] = None
    html: str = ""
    markdown: str = ""
    title: str = ""
    render_mode: Optional[str] = None          # static | playwright
    encoding: str = ""
    from_cache: bool = False
    content_hash: Optional[str] = None
    changed: bool = False                      # content differs from the previously stored copy
    error: Optional[str] = None                # see ERROR KINDS in the docstring of engine.get
    notes: list = field(default_factory=list)
    fetched_at: Optional[datetime] = None
    robots_status: str = "unknown"
    network: bool = False                      # a network attempt was made (for ingest.page error rows)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.html)


class FetchEngine:
    def __init__(self, cache: PageCache, static, gate: Gate, renderer=None, hints: Optional[Hints] = None,
                 offline: bool = False, allow_render: bool = True) -> None:
        self.cache, self.static, self.gate, self.renderer = cache, static, gate, renderer
        self.hints, self.offline, self.allow_render = hints or Hints(), offline, allow_render
        self.stats = {"cache_hits": 0, "static": 0, "rendered": 0, "errors": 0}

    # ---- helpers ----
    @property
    def renderer_ok(self) -> bool:
        return self.renderer is not None and self.allow_render

    @property
    def domains(self) -> list:
        return self.gate.domains

    def begin(self, domains: list, budget: int) -> None:
        self.gate.begin(domains, budget)
        self.stats = {"cache_hits": 0, "static": 0, "rendered": 0, "errors": 0}

    def close(self) -> None:
        if self.renderer is not None:
            self.renderer.close()

    def read_robots(self, base: str):
        """Robots reader for the gate: plain client first, the browser when the plain client is refused."""
        status, body, kind = self.static.get_text(base + "/robots.txt")
        if (status in (403, 412) or kind in ("blocked", "challenge")) and self.renderer_ok and not self.offline:
            st, bd = self.renderer.get_text(base + "/robots.txt")
            if st is not None:
                status, body = st, bd
        return interpret_robots(status, body)

    def _fail(self, url: str, error: str, notes=None, robots: str = "unknown", network: bool = False,
              status: Optional[int] = None) -> FetchedPage:
        self.stats["errors"] += 1
        return FetchedPage(url, error=error, notes=list(notes or []), robots_status=robots, network=network, status=status)

    def _page(self, url, final, html, status, mode, enc, from_cache, fetched_at, stored_hash, notes) -> FetchedPage:
        c = html_to_markdown(html, final)
        h = stored_hash or md5_text(c.markdown)
        if c.fallback:
            notes = list(notes) + ["clean_fallback"]
        return FetchedPage(url, final, status, html, c.markdown, c.title, mode, enc, from_cache, h, False, None, list(notes),
                           fetched_at)

    # ---- main entry ----
    def get(self, url: str, mode: str = "auto", wait_selector: Optional[str] = None,
            needs_render: Optional[Callable[[str], bool]] = None) -> FetchedPage:
        """mode: auto | static | render. ERROR KINDS: off_domain, offline_miss, robots_disallowed, robots_unreadable,
        host_blocked, budget, timeout, connect_error, not_html, http_<status>, redirect_off_domain, needs_renderer,
        challenge_page, render_unavailable:*, render_failed:*, render_http_<status>."""
        url = clean_url(url)
        if not self.gate.on_domain(url):
            return self._fail(url, "off_domain")
        hint = self.hints.lookup(url)
        if mode == "auto" and hint.render == "always":
            mode = "render"
        elif mode == "auto" and hint.render == "never":
            mode = "static"
        if mode == "render" and not self.allow_render:
            mode = "static"
        wait_selector = wait_selector or hint.wait_selector
        check = needs_render or looks_like_js_shell
        notes: list = []

        cached = self.cache.read(url)
        if cached is not None and cached.status == 200:
            usable = not (mode == "render" and cached.render_mode != "playwright")
            if usable and mode == "auto" and cached.render_mode == "static" and check(cached.html):
                if self.renderer_ok:
                    usable = False
                else:
                    notes.append("js_shell_unrendered")
            if usable:
                self.stats["cache_hits"] += 1
                return self._page(url, cached.final_url, cached.html, cached.status, cached.render_mode, cached.encoding,
                                  True, cached.fetched_at, cached.content_hash, notes)
        if self.offline:
            return self._fail(url, "offline_miss")

        d = self.gate.check(url)
        if not d.allowed:
            return self._fail(url, d.reason, robots=d.robots_status)
        host = host_of(url)
        html = enc = final = None
        status, render_mode = None, "static"
        use_static = mode != "render" and not self.gate.static_blocked(host)
        escalate = not use_static

        if use_static:
            self.gate.spend()
            self.stats["static"] += 1
            sr = self.static.get(url)
            if sr.kind == "ok":
                if not self.gate.on_domain(sr.final_url):
                    self.gate.note_failure(host)
                    return self._fail(url, "redirect_off_domain", robots=d.robots_status, network=True)
                if looks_like_challenge(sr.html):
                    self.gate.note_static_block(host)
                    escalate = True
                    notes.append("challenge_page")
                elif mode == "auto" and check(sr.html):
                    if self.renderer_ok:
                        escalate = True
                        notes.append("js_shell")
                    else:
                        notes.append("js_shell_unrendered")
                if not escalate:
                    html, enc, final, status = sr.html, sr.encoding, sr.final_url, 200
            elif sr.kind in ("blocked", "challenge"):
                self.gate.note_static_block(host)
                escalate = True
                notes.append(f"static_{sr.kind}_{sr.status}")
            else:
                self.gate.note_failure(host)
                err = {"timeout": "timeout", "connect_error": "connect_error", "not_html": "not_html"}.get(
                    sr.kind, f"http_{sr.status}" if sr.status else "http_error")
                return self._fail(url, err, notes, d.robots_status, True, sr.status)

        if escalate:
            if not self.renderer_ok:
                self.gate.note_failure(host)
                return self._fail(url, "needs_renderer", notes, d.robots_status, use_static)
            self.gate.spend()
            self.stats["rendered"] += 1
            rr = self.renderer.render(url, wait_selector, hint.settle_ms)
            notes += list(rr.notes)
            if rr.error:
                self.gate.note_failure(host)
                return self._fail(url, rr.error, notes, d.robots_status, True)
            if rr.status and rr.status >= 400:
                self.gate.note_failure(host)
                return self._fail(url, f"render_http_{rr.status}", notes, d.robots_status, True, rr.status)
            if not self.gate.on_domain(rr.final_url):
                self.gate.note_failure(host)
                return self._fail(url, "redirect_off_domain", notes, d.robots_status, True)
            if looks_like_challenge(rr.html):
                self.gate.note_failure(host)
                return self._fail(url, "challenge_page", notes, d.robots_status, True)
            html, enc, final, status, render_mode = rr.html, "utf-8", rr.final_url, rr.status or 200, "playwright"

        self.gate.note_success(host)
        page = self._page(url, final, html, status, render_mode, enc, False, None, None, notes)
        prev = self.cache.read(url, allow_stale=True)
        if prev is not None and prev.render_mode == render_mode:
            prev_hash = prev.content_hash or md5_text(html_to_markdown(prev.html, prev.final_url).markdown)
            page.changed = prev_hash != page.content_hash
        page.fetched_at = self.cache.write(url, final, html, status, render_mode, enc, page.content_hash)
        page.robots_status, page.network = d.robots_status, True
        return page
