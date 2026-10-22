"""Shared fakes for the fetch tests: no network, no browser, no database."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from profinder_pipeline.fetch.cache import PageCache
from profinder_pipeline.fetch.engine import FetchEngine
from profinder_pipeline.fetch.gate import Gate, RetryPolicy, Throttle
from profinder_pipeline.fetch.hints import Hints
from profinder_pipeline.fetch.render import RenderResult
from profinder_pipeline.fetch.static import StaticFetcher

FIX = Path(__file__).parent / "fixtures" / "fetch"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, url=""):
        self.status_code, self.content, self.url = status, body if isinstance(body, bytes) else body.encode("utf-8"), url
        self.headers = {"content-type": "text/html; charset=utf-8", **(headers or {})}


class FakeClient:
    """routes: url -> FakeResponse | Exception | list of those (consumed in order, the last one repeats)."""

    def __init__(self, routes=None):
        self.routes, self.calls = dict(routes or {}), []

    def get(self, url):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            r = FakeResponse(404, b"not found", url=url)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        if not r.url:
            r.url = url
        return r

    def page_calls(self):
        return [u for u in self.calls if not u.endswith("/robots.txt")]


class FakeRenderer:
    def __init__(self, pages=None, robots=None, error=None, status=200):
        self.pages, self.robots, self.error, self.status = dict(pages or {}), robots, error, status
        self.render_calls, self.text_calls = [], []

    def render(self, url, wait_selector=None, settle_ms=1000):
        self.render_calls.append((url, wait_selector))
        if self.error:
            return RenderResult(None, url, "", self.error)
        return RenderResult(self.status, url, self.pages.get(url, "<html><body>" + "x " * 400 + "</body></html>"), None, [])

    def get_text(self, url):
        self.text_calls.append(url)
        return (200, self.robots) if self.robots is not None else (None, "")

    def close(self):
        pass


class Clock:
    def __init__(self, t=1000.0):
        self.t, self.sleeps = t, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make_engine(tmp_path, routes=None, renderer=None, domains=("example.edu.cn",), budget=50, hints=None, offline=False,
                clock=None, now=None):
    clock = clock or Clock()
    client = FakeClient(routes)
    throttle = Throttle(2.5, 0.0, clock.now, clock.sleep, lambda: 0.0)
    static = StaticFetcher(client, throttle, RetryPolicy(3, 2.0, 30.0), clock.sleep)
    holder = {}
    gate = Gate(lambda base: holder["e"].read_robots(base))
    cache = PageCache(tmp_path / "cache", now=now or (lambda: datetime.now(timezone.utc)))
    engine = FetchEngine(cache, static, gate, renderer, hints or Hints(), offline=offline)
    holder["e"] = engine
    engine.begin(list(domains), budget)
    engine.client, engine.clock = client, clock
    return engine


def ok_page(body="<html><body><p>" + "hello world " * 60 + "</p></body></html>"):
    return FakeResponse(200, body)
