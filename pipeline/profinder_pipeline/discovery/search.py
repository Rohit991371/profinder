from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..registry.textnorm import host_allowed, host_of, norm_en, registrable_domain

TAVILY_URL = "https://api.tavily.com/search"
SERPER_URL = "https://google.serper.dev/search"
QUERIES = {
    "faculty_list": "{name} computer science faculty list",
    "admissions": "{name} international students admissions",
    "scholarship": "{name} international students scholarship",
}


class SearchUnavailable(RuntimeError):
    """Key rejected, plan limit reached or call budget used up: stop searching, keep crawling."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""


def parse_tavily(data: dict) -> list[SearchHit]:
    return [SearchHit(r.get("url", ""), r.get("title", ""), r.get("content", "")) for r in (data.get("results") or []) if r.get("url")]


def parse_serper(data: dict) -> list[SearchHit]:
    return [SearchHit(r.get("link", ""), r.get("title", ""), r.get("snippet", "")) for r in (data.get("organic") or []) if r.get("link")]


class SearchClient:
    """Tavily (basic search = 1 credit) or Serper. Raw responses are cached on disk, so a re-run costs no credits.
    API keys are only sent in headers and never logged. Request/response shapes are from the provider docs (unverified: see `probe-search`)."""

    def __init__(self, provider: str, api_key: str, client, cache_dir: Path, max_calls: int = 150) -> None:
        self.provider, self.key, self.client, self.cache_dir, self.max_calls = provider, api_key, client, Path(cache_dir), max_calls
        self.calls = 0
        self.disabled_reason: str | None = None

    def _cache(self, name: str, kind: str) -> Path:
        return self.cache_dir / "search" / self.provider / f"{norm_en(name)}__{kind}.json"

    def _request(self, query: str, domains: list[str]) -> dict:
        if self.provider == "tavily":
            r = self.client.post(TAVILY_URL, headers={"Authorization": f"Bearer {self.key}"}, timeout=40,
                                 json={"query": query, "search_depth": "basic", "max_results": 8, "include_domains": domains})
        else:
            dom = next((d for d in map(registrable_domain, domains) if d), domains[0])
            r = self.client.post(SERPER_URL, headers={"X-API-KEY": self.key}, timeout=40, json={"q": f"{query} site:{dom}", "num": 8})
        if r.status_code in (401, 403, 429, 432, 433):
            raise SearchUnavailable(f"{self.provider} HTTP {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"{self.provider} HTTP {r.status_code}")
        return r.json()

    def search(self, name: str, kind: str, domains: list[str]) -> list[SearchHit]:
        if self.disabled_reason:
            raise SearchUnavailable(self.disabled_reason)
        p = self._cache(name, kind)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
        else:
            if self.calls >= self.max_calls:
                self.disabled_reason = f"max search calls ({self.max_calls}) reached"
                raise SearchUnavailable(self.disabled_reason)
            try:
                data = self._request(QUERIES[kind].format(name=name), domains)
            except SearchUnavailable as e:
                self.disabled_reason = str(e)
                raise
            self.calls += 1
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        hits = parse_tavily(data) if self.provider == "tavily" else parse_serper(data)
        return [h for h in hits if host_allowed(host_of(h.url), domains)]
