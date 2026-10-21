from __future__ import annotations

from dataclasses import dataclass, field

from ..registry.textnorm import host_allowed, host_of
from .classify import classify, cs_related, is_personal_page, page_title
from .fetcher import Page
from .links import Link, visible_text
from .scoring import score_link
from .search import SearchUnavailable

KINDS = ("faculty_list", "admissions", "scholarship")


@dataclass
class Found:
    kind: str
    url: str
    method: str                 # seed | link_follow | search
    validated: bool | None      # True = page looks right; False = fetched but did not; None = seed not judged
    score: float = 0.0
    via: str | None = None
    note: str = ""


@dataclass
class UniInput:
    name: str
    university_id: str | None
    start_urls: list[str]
    domains: list[str]
    seed_urls: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Result:
    name: str
    university_id: str | None
    found: list[Found] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    start_url: str | None = None
    pages_fetched: int = 0
    cache_hits: int = 0
    blocked: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    offdomain: list[str] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)


_SUBUNIT = ("college", "school of", "department of", "institute", "center", "centre", "学院", "研究院", "中心")


def rank_hit(kind: str, url: str, title: str) -> float:
    """Order search hits before spending fetches: keyword score of title+url, personal homepages last, sub-units slightly lower."""
    s = score_link(kind, title, url)
    if is_personal_page(url, title):
        s -= 20
    if kind != "faculty_list" and any(w in (title or "").lower() for w in _SUBUNIT):
        s -= 3
    return s


_SUB_SITE_LABELS = {"en", "english", "eng", "global", "international", "iso", "isc", "ic", "oic", "study", "admission", "admissions"}


def _is_sub_site(host: str | None) -> bool:
    label = (host or "").split(".")[0]
    return label in _SUB_SITE_LABELS or "english" in label


class Discoverer:
    def __init__(self, fetcher, search=None) -> None:
        self.f, self.search = fetcher, search

    def _why(self, kind: str, page: Page, from_search: bool = False) -> str | None:
        """None = acceptable; otherwise a short reason code (shown by `discover revalidate`)."""
        if kind in ("admissions", "scholarship") and is_personal_page(page.final_url or page.url, page_title(page.html)):
            return "personal_page"
        if not classify(kind, page.html, page.links):
            if kind == "scholarship":
                t = visible_text(page.html).lower()
                return "too_few_scholarship_words" if t.count("scholarship") + t.count("奖学金") < 2 else "no_international_marker"
            return {"faculty_list": "not_a_static_faculty_list", "admissions": "no_admission_and_international_words"}[kind]
        if from_search and kind == "faculty_list" and not cs_related(visible_text(page.html)):
            return "not_computing_related"
        return None

    def _valid(self, kind: str, page: Page, from_search: bool = False) -> bool:
        return self._why(kind, page, from_search) is None

    # ---- helpers ----
    def _best(self, page: Page, kind: str, k: int, min_score: float, off: list[str] | None = None) -> list[tuple[float, Link]]:
        scored = []
        for l in page.links:
            if l.url in (page.url, page.final_url):
                continue
            s = score_link(kind, l.text, l.url)
            if s < min_score:
                continue
            if host_allowed(host_of(l.url), self.f.domains):
                scored.append((s, l))
            elif off is not None and len(off) < 6:
                off.append(f"{kind}:{l.url}")
        scored.sort(key=lambda t: (-t[0], len(t[1].url)))
        return scored[:k]

    def _found(self, kind, page: Page, method, validated, score, via=None, note="") -> Found:
        return Found(kind, page.final_url or page.url, method, validated, round(score, 1), via, note)

    # ---- crawl routines ----
    def _crawl_simple(self, kind: str, home: Page, off: list[str]):
        tried: set[str] = set()
        unval: Found | None = None
        for s1, l1 in self._best(home, kind, 4, 4, off):
            p1 = self.f.get(l1.url)
            tried.add(l1.url)
            if not p1:
                continue
            if self._valid(kind, p1):
                return self._found(kind, p1, "link_follow", True, s1, home.url), None
            if s1 >= 8 and unval is None:
                unval = self._found(kind, p1, "link_follow", False, s1, home.url, "page did not look like the target (maybe JavaScript)")
            for s2, l2 in self._best(p1, kind, 2, 5, off):
                if l2.url in tried:
                    continue
                tried.add(l2.url)
                p2 = self.f.get(l2.url)
                if p2 and self._valid(kind, p2):
                    return self._found(kind, p2, "link_follow", True, s2, p1.url), None
        return None, unval

    def _departments(self, home: Page, off: list[str]) -> list[tuple[float, Link]]:
        """Computing-related school links: on the homepage, else via the school index (机构设置/院系设置 ...), else via the
        university-level 师资队伍 page, which usually links to a 'faculty by school' page (学院师资)."""
        depts: list[tuple[float, Link]] = []

        def add(page: Page) -> None:
            for sc, l in self._best(page, "department", 4, 5, off):
                if all(l.url != d[1].url for d in depts):
                    depts.append((sc, l))

        add(home)
        if len(depts) < 2:
            for _, l in [*self._best(home, "dept_index", 2, 3, off), *self._best(home, "faculty_list", 2, 6, off)]:
                p = self.f.get(l.url)
                if not p:
                    continue
                add(p)
                if len(depts) < 2:
                    for _, l2 in [*self._best(p, "school_faculty", 1, 4, off), *self._best(p, "dept_index", 1, 3, off)]:
                        p2 = self.f.get(l2.url)
                        if p2:
                            add(p2)
                if len(depts) >= 3:
                    break
        return sorted(depts, key=lambda t: -t[0])

    def _crawl_faculty(self, home: Page, off: list[str]):
        depts = self._departments(home, off)
        unval: Found | None = None
        tried: set[str] = set()
        seen_depts: set[str] = set()
        for sd, d in depts[:4]:
            if d.url in seen_depts:
                continue
            seen_depts.add(d.url)
            dp = self.f.get(d.url)
            if not dp:
                continue
            for sf, fl in self._best(dp, "faculty_list", 3, 4, off):
                if fl.url in tried:
                    continue
                tried.add(fl.url)
                fp = self.f.get(fl.url)
                if not fp:
                    continue
                if self._valid("faculty_list", fp):
                    return self._found("faculty_list", fp, "link_follow", True, sf, dp.url, f"department page: {dp.url}"), None
                if sf >= 8 and unval is None:
                    unval = self._found("faculty_list", fp, "link_follow", False, sf, dp.url, "page did not look like a static faculty list (maybe JavaScript)")
            if self._valid("faculty_list", dp):
                return self._found("faculty_list", dp, "link_follow", True, sd, home.url, "department page is itself the list"), None
        return None, unval

    # ---- main ----
    def run(self, uni: UniInput, budget: int, kinds: tuple[str, ...] = KINDS) -> Result:
        f = self.f
        f.begin(uni.domains, budget)
        res = Result(uni.name, uni.university_id)
        found: dict[str, Found] = {}
        unval: dict[str, Found] = {}

        for kind, urls in uni.seed_urls.items():           # 1. manual seeds are trusted, but must be reachable
            if kind not in kinds or kind in found:
                continue
            for u in urls:
                p = f.get(u)
                if p is None:
                    res.notes.append(f"seed_unreachable:{u}")
                    continue
                ok = self._valid(kind, p)
                found[kind] = self._found(kind, p, "seed", ok, 0, None, "" if ok else "seed page not list-like in static HTML (maybe JavaScript)")
                break

        homes: list[Page] = []                              # 2. link crawl from the official site
        for s_url in uni.start_urls:
            p = self.f.get(s_url)
            if p and all(host_of(p.final_url) != host_of(h.final_url) for h in homes):
                homes.append(p)
                if res.start_url is None:
                    res.start_url = s_url
            # the registry sometimes proposes an English sub-site (en.cau.edu.cn): then also try the main site
            if homes and (len(homes) >= 2 or not _is_sub_site(host_of(homes[0].final_url))):
                break
        res.start_url = res.start_url or (uni.start_urls[0] if uni.start_urls else None)
        if not homes and any(k not in found for k in kinds):
            res.notes.append("homepage_unreachable")
        for kind in kinds:
            if kind in found:
                continue
            for home in homes:
                hit, uv = self._crawl_faculty(home, res.offdomain) if kind == "faculty_list" else self._crawl_simple(kind, home, res.offdomain)
                if hit:
                    found[kind] = hit
                    break
                if uv and kind not in unval:
                    unval[kind] = uv

        if self.search is not None:                         # 3. search fallback (finds URLs only; the fetcher validates them)
            for kind in kinds:
                if kind in found:
                    continue
                try:
                    hits = self.search.search(uni.name, kind, uni.domains)
                except SearchUnavailable as e:
                    res.notes.append(f"search_unavailable:{e}")
                    break
                except Exception as e:
                    res.notes.append(f"search_error:{type(e).__name__}")
                    continue
                res.searched.append(kind)
                for h in sorted(hits, key=lambda h: -rank_hit(kind, h.url, h.title))[:4]:
                    p = f.get(h.url)
                    if p and self._valid(kind, p, from_search=True):
                        found[kind] = self._found(kind, p, "search", True, 0, None, h.title[:80])
                        break

        for kind in kinds:                                  # 4. keep an unvalidated crawl candidate rather than claim a miss
            if kind not in found and kind in unval:
                found[kind] = unval[kind]

        res.found = [found[k] for k in kinds if k in found]
        res.missing = [k for k in kinds if k not in found]
        res.pages_fetched, res.cache_hits = f.used, f.cache_hits
        res.blocked, res.errors = list(f.blocked), list(f.errors)[:12]
        if f.exhausted:
            res.notes.append("page_budget_exhausted")
        return res
