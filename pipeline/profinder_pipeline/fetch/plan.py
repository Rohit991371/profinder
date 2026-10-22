"""Faculty two-stage planner (task 3.5): list page(s) (+ pagination) -> profile URLs -> (caller fetches profile pages).

This module finds LINKS to people; it does not extract or validate people (Iteration 4)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..registry.textnorm import host_allowed, host_of
from .clean import strip_noise
from .engine import FetchEngine, FetchedPage, looks_like_js_shell

CJK = "\u3400-\u9fff"
_ZH_NAME = re.compile(rf"^[{CJK}](?:[\u3000 ]?[{CJK}]|·[{CJK}]){{1,5}}$")
_EN_NAME = re.compile(r"^[A-Z][A-Za-z'’.\-]+(?: [A-Z][A-Za-z'’.\-]+){1,3}$")
_PAREN = re.compile(r"[（(][^）)]{0,20}[）)]\s*$")
ZH_STOP = ("首页", "概况", "简介", "新闻", "通知", "公告", "招生", "教学", "科研", "师资", "学院", "学校", "学科", "党建",
           "联系", "登录", "搜索", "更多", "返回", "下载", "招聘", "校友", "教育", "管理", "服务", "合作", "中心", "研究",
           "机构", "动态", "专业", "本科", "校园", "队伍", "名录", "导航", "友情", "链接", "版权", "地图", "网站", "详情",
           "实验室", "研究所", "系所", "办公室", "委员会", "院士", "学者", "人才", "荣誉", "简体", "繁體", "中文", "英文")
EN_STOP = frozenset("""college school department faculty directory overview introduction news notice research teaching student
students alumni contact home about programs program admissions admission centre center institute university laboratory lab
party staff teacher teachers talent recruitment service services guide download zone login search more page rules regulations
academic academics education management engineering science sciences technology computer information cyberspace security
intelligence systems software people profile profiles homepage list members member leaders leadership history development
organization organizational structure cooperation international office offices committee numbers map campus join us film
hospital life vacancy procedures application overview academy""".split())
MIN_PEOPLE_FOR_LIST = 5
GENERIC_LABELS = {"more", "详情", "更多", "查看", "read more", "details", "detail", "profile", "homepage", "visit", ">>", "more>>",
                  "个人主页", "主页", "查看详情", "了解更多"}
NEXT_TEXT = {"下一页", "下页", "下一頁", "后一页", "next", "nextpage", "next>", "›", ">", "»", ">>", "下一页>"}


def person_label(text: str) -> Optional[str]:
    """The label if it looks like a person's name (Chinese 2-6 characters incl. 蒋　力 and 约翰·霍普; English 2-4 capitalised words)."""
    t = _PAREN.sub("", (text or "").strip()).strip()
    t = re.sub(r"[ \t\r\n]+", " ", t)
    if not t or len(t) > 40:
        return None
    if _ZH_NAME.match(t):
        return None if any(s in t for s in ZH_STOP) else t
    if _EN_NAME.match(t):
        words = {w.lower().strip(".'’-") for w in t.split()}
        return None if words & EN_STOP else t
    return None


@dataclass
class Person:
    name: str
    url: Optional[str]


@dataclass
class PeopleScan:
    people: list = field(default_factory=list)       # [Person]
    off_domain: int = 0                              # name-like links to hosts outside the university's domains (not followed)
    no_link: int = 0                                 # names without any link
    max_cluster: int = 0                             # most name-like links inside ONE list/table/block (a real list has a big one)


def _clean_href(base: str, href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    full = urldefrag(urljoin(base, href))[0]
    return full if urlsplit(full).scheme in ("http", "https") else None


def extract_people(html: str, page_url: str, domains: list) -> PeopleScan:
    """Name-like links in the page body. Layout noise (menus, footers) is dropped first; if that leaves too few people the
    scan is repeated with tag-level cleaning only (some sites put the real list in a block that looks like navigation)."""
    scan = _scan(strip_noise(BeautifulSoup(html or "", "lxml"), aggressive=True), page_url, domains)
    if len([p for p in scan.people if p.url]) < MIN_PEOPLE_FOR_LIST:
        wide = _scan(strip_noise(BeautifulSoup(html or "", "lxml"), aggressive=False), page_url, domains)
        if len([p for p in wide.people if p.url]) > len([p for p in scan.people if p.url]):
            return wide
    return scan


def _scan(soup, page_url: str, domains: list) -> PeopleScan:
    scan, seen = PeopleScan(), set()
    clusters: dict = {}
    for a in soup.find_all("a"):
        label = person_label(a.get_text(" ", strip=True))
        if label is None and a.get_text(" ", strip=True).strip().lower() in GENERIC_LABELS:
            box = a.find_parent(["li", "tr", "dl"]) or a.parent
            if box is not None:
                for el in box.find_all(["h3", "h4", "h5", "strong", "b", "span", "p", "td", "dt"]):
                    label = person_label(el.get_text(" ", strip=True))
                    if label:
                        break
        if label is None:
            continue
        box = a.find_parent(["ul", "ol", "dl", "table"]) or (a.parent.parent if a.parent is not None and a.parent.parent is not None else a.parent)
        clusters[id(box)] = clusters.get(id(box), 0) + 1
        url = _clean_href(page_url, a.get("href", ""))
        if url is None:
            key = ("name", label)
            if key not in seen:
                seen.add(key)
                scan.no_link += 1
                scan.people.append(Person(label, None))
            continue
        if url == urldefrag(page_url)[0]:
            continue
        if not host_allowed(host_of(url), domains):
            scan.off_domain += 1
            continue
        if ("url", url) in seen:
            continue
        seen.add(("url", url))
        scan.people.append(Person(label, url))
    scan.max_cluster = max(clusters.values(), default=0)
    return scan


def people_count(html: str, page_url: str, domains: list) -> int:
    return len([p for p in extract_people(html, page_url, domains).people if p.url])


# ---------------- pagination ----------------
@dataclass
class Pagination:
    next_url: Optional[str] = None
    numbered: list = field(default_factory=list)
    letters: list = field(default_factory=list)
    js_pagination: bool = False                      # a "next" control that needs JavaScript (not followed)


def find_pagination(html: str, page_url: str) -> Pagination:
    soup = BeautifulSoup(html or "", "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    here = urldefrag(page_url)[0]
    here_dir = urlsplit(here).path.rsplit("/", 1)[0]
    pg = Pagination()
    numbered: dict = {}
    letters: dict = {}
    for a in soup.find_all("a"):
        text = re.sub(r"\s+", "", a.get_text("", strip=True)).lower()
        raw = (a.get("href") or "").strip()
        full = _clean_href(page_url, raw)
        if text in NEXT_TEXT:
            if full is None:
                pg.js_pagination = True
            elif full != here:
                pg.next_url = full
            continue
        if full is None or full == here:
            continue
        if text.isdigit() and len(text) <= 3 and urlsplit(full).path.rsplit("/", 1)[0] == here_dir \
                and host_of(full) == host_of(here):
            numbered[int(text)] = full
        elif len(text) == 1 and text.isalpha() and text.isascii():
            sp = urlsplit(full)
            shape = (sp.netloc, sp.path, tuple(sorted(k for k, _ in parse_qsl(sp.query))))
            letters.setdefault(shape, {})[text] = full
    pg.numbered = [numbered[k] for k in sorted(numbered)]
    big = [d for d in letters.values() if len(d) >= 10]
    if big:
        pg.letters = [u for _, u in sorted(max(big, key=len).items())]
    return pg


# ---------------- plan ----------------
@dataclass
class FacultyPlan:
    list_url: str
    kind: str = "unknown"                            # faculty_list | not_a_faculty_list | list_unreachable
    list_pages: list = field(default_factory=list)   # [{url, ok, error, render_mode, from_cache, people}]
    people: list = field(default_factory=list)       # [Person] deduplicated
    profile_urls: list = field(default_factory=list)
    off_domain: int = 0
    no_link: int = 0
    flags: list = field(default_factory=list)        # js_pagination, list_truncated_by_budget, needs_renderer, ...
    first_error: Optional[str] = None
    max_cluster: int = 0


def looks_like_list(total_with_profile: int, max_cluster: int) -> bool:
    """A faculty list has one block with >= 5 name links, or at least 10 name links overall (spread over pages / small blocks).
    Menus and school lists produce a few scattered name-like labels and fail both."""
    return max_cluster >= MIN_PEOPLE_FOR_LIST or total_with_profile >= 10


def plan_faculty(engine: FetchEngine, list_url: str, max_list_pages: int = 40) -> FacultyPlan:
    plan = FacultyPlan(list_url)
    domains = engine.domains
    queue, seen = [list_url], set()
    by_key: dict = {}
    max_cluster = 0

    def needs_render(h: str, u: str = "") -> bool:
        return people_count(h, u or list_url, domains) < MIN_PEOPLE_FOR_LIST and looks_like_js_shell(h)

    while queue and len(plan.list_pages) < max_list_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        fp: FetchedPage = engine.get(url, "auto", needs_render=lambda h, u=url: needs_render(h, u))
        row = {"url": url, "ok": fp.ok, "error": fp.error, "render_mode": fp.render_mode, "from_cache": fp.from_cache, "people": 0,
               "notes": fp.notes, "fp": fp}
        plan.list_pages.append(row)
        if not fp.ok:
            if url == list_url:
                plan.first_error = fp.error
                plan.kind = "list_unreachable"
            elif fp.error == "budget":
                plan.flags.append("list_truncated_by_budget")
                break
            continue
        scan = extract_people(fp.html, fp.final_url or url, domains)
        row["people"] = len([p for p in scan.people if p.url])
        max_cluster = max(max_cluster, scan.max_cluster)
        plan.off_domain += scan.off_domain
        plan.no_link += scan.no_link
        for p in scan.people:
            by_key.setdefault(("url", p.url) if p.url else ("name", p.name), p)
        if "js_shell_unrendered" in fp.notes and "needs_renderer" not in plan.flags:
            plan.flags.append("needs_renderer")
        pg = find_pagination(fp.html, fp.final_url or url)
        if pg.js_pagination and not (pg.next_url or pg.numbered):
            if "js_pagination" not in plan.flags:
                plan.flags.append("js_pagination")
        for nxt in ([pg.next_url] if pg.next_url else []) + pg.numbered + pg.letters:
            if nxt not in seen and nxt not in queue and engine.gate.on_domain(nxt):
                queue.append(nxt)
    if queue and len(plan.list_pages) >= max_list_pages:
        plan.flags.append("list_truncated_by_max_pages")

    plan.max_cluster = max_cluster
    plan.people = list(by_key.values())
    page_urls = {r["url"] for r in plan.list_pages}
    plan.profile_urls = [p.url for p in plan.people if p.url and p.url not in page_urls]
    if plan.kind != "list_unreachable":
        plan.kind = "faculty_list" if looks_like_list(len(plan.profile_urls), max_cluster) else "not_a_faculty_list"
        truncated = any(f.startswith("list_truncated") for f in plan.flags)
        if truncated and plan.kind == "not_a_faculty_list" and plan.profile_urls:
            plan.kind = "list_incomplete"                       # stopped early (budget / page limit): do not judge the target
    return plan
