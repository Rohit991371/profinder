"""CSS extractor framework (task 4.1). Driven by config/sites/<slug>.yml; no per-site Python.

Field spec keys (first match wins): emails | previous | attr_of_item | labelled_link | label | section | section_items | css(+attr,index).
Optional on any spec: `closest` (selector of an ancestor to search inside), `css` (region inside the item/ancestor).
Everything returns plain str / list[str] / None; validation and grounding happen later (validate/)."""
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin

import soupsieve as sv
from bs4 import BeautifulSoup, Tag

from ..utils.urls import canonical_url, host_in_domains
from .emailtools import find_emails_loose
from .sitecfg import SiteConfig

PARSER = "lxml"
_URL_RE = re.compile(r"https?://[^\s<>\"'）)]+")


@dataclass
class RawPerson:
    name_raw: Optional[str]
    profile_href: Optional[str] = None       # absolute URL exactly as linked on the page
    position_raw: Optional[str] = None
    emails: list[str] = field(default_factory=list)
    department: Optional[str] = None
    group: Optional[str] = None              # e.g. rank heading; evidence only
    stage: str = "list"


@dataclass
class ProfileData:
    name: Optional[str] = None
    position: Optional[str] = None
    emails: list[str] = field(default_factory=list)
    department: Optional[str] = None
    personal_website: Optional[str] = None
    interests_lines: Optional[list[str]] = None
    text: str = ""


@dataclass
class ListParse:
    people: list[RawPerson]
    raw_items: int
    skipped_not_person_link: int
    duplicate_urls: int
    page_text: str


# ----------------------------------------------------------------------------------------------- helpers
def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, PARSER)


def clean_ws(s: Optional[str]) -> str:
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", s or "").strip()


def visible_text(html: str) -> str:
    """All text of the page (scripts/styles removed, hidden elements kept), one text node per line."""
    soup = soup_of(html)
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    lines = [clean_ws(x) for x in soup.get_text("\n").split("\n")]
    return "\n".join(x for x in lines if x)


def _lines(el: Tag) -> list[str]:
    out: list[str] = []
    for s in el.stripped_strings:
        for part in str(s).split("\n"):
            part = clean_ws(part)
            if part:
                out.append(part)
    return out


def _el_text(el: Tag) -> str:
    return clean_ws(el.get_text(" ", strip=True))


def _select(scope: Tag, css: str, index: int = 0) -> Optional[Tag]:
    if css == ":scope":
        return scope
    found = scope.select(css)
    return found[index] if len(found) > index else None


def _closest(node: Tag, css: str) -> Optional[Tag]:
    cur: Optional[Tag] = node
    while cur is not None and isinstance(cur, Tag):
        if sv.match(css, cur):
            return cur
        cur = cur.parent
    return None


def _previous(node: Tag, css: str) -> Optional[str]:
    for el in node.find_all_previous(True):
        if sv.match(css, el):
            t = _el_text(el)
            if t:
                return t
    return None


def _abs(base_url: str, value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return urljoin(base_url, value.strip())


def _cut_at_stop(lines: list[str], stop_titles: list[str], max_lines: int) -> list[str]:
    stops = {re.sub(r"[：:\s]", "", t) for t in stop_titles}
    out: list[str] = []
    for ln in lines:
        if re.sub(r"[：:\s]", "", ln) in stops:
            break
        out.append(ln)
    return out[:max_lines]


def section_lines(root: Tag, container: Optional[str], boundary: str, titles: list[str],
                  stop_titles: Optional[list[str]] = None, max_lines: int = 12) -> Optional[list[str]]:
    """Lines under the heading whose title is in `titles`. A heading is the text of a `boundary` element, or - when that
    element is empty (the HTML parser moved its <p> out, as on Tsinghua profiles) - the element right after it."""
    cont = root.select_one(container) if container else root
    if cont is None:
        return None
    kids = [c for c in cont.children if isinstance(c, Tag)]
    wanted = {re.sub(r"[：:\s]", "", t) for t in titles}
    blocks: list[tuple[str, list[str]]] = []
    cur: Optional[tuple[str, list[str]]] = None
    i = 0
    while i < len(kids):
        k = kids[i]
        if sv.match(boundary, k):
            heading = _el_text(k)
            j = i + 1
            if not heading and j < len(kids):
                heading = _el_text(kids[j])
                j += 1
            cur = (re.sub(r"[：:\s]", "", heading), [])
            blocks.append(cur)
            i = j
            continue
        if cur is not None:
            if k.name in ("ul", "ol"):
                cur[1].extend(clean_ws(li.get_text(" ", strip=True)) for li in k.find_all("li") if _el_text(li))
            else:
                cur[1].extend(_lines(k))
        i += 1
    for title, lines in blocks:
        if title in wanted and lines:
            # Some pages have no heading element between two sections (e.g. 研究领域 followed by 讲授课程): stop at the next known title.
            cut = _cut_at_stop(lines, stop_titles or [], max_lines)
            return cut or None
    return None


def section_items(root: Tag, item: str, title: str, body: str, titles: list[str], line_css: Optional[str] = None,
                  strip_prefix: Optional[str] = None, max_lines: int = 12) -> Optional[list[str]]:
    """Items that have a title element and a body element (SJTU bio items, ECNU `div.post`). `line_css`: one line per matching
    element inside the body (default: one line per text node); `strip_prefix`: regex removed from the start of each line
    (e.g. the label '目前研究方向：')."""
    wanted = {re.sub(r"[：:\s]", "", t) for t in titles}
    for it in root.select(item):
        t = it.select_one(title)
        if t is not None and re.sub(r"[：:\s]", "", _el_text(t)) in wanted:
            b = it.select_one(body)
            if b is None:
                continue
            raw = [clean_ws(e.get_text("", strip=True)) for e in b.select(line_css)] if line_css else _lines(b)
            lines = []
            for ln in raw:
                if strip_prefix:
                    ln = clean_ws(re.sub(strip_prefix, "", ln))
                if ln:
                    lines.append(ln)
            if lines:
                return lines[:max_lines]
    return None


def extract_field(node: Tag, spec: dict[str, Any], base_url: str) -> Any:
    scope = node
    if "closest" in spec:
        scope = _closest(node, spec["closest"])
        if scope is None:
            return None
    if "emails" in spec:
        region = _select(scope, spec["css"]) if spec.get("css") else scope
        if region is None:
            return []
        found = find_emails_loose("\n".join(_lines(region)))
        for a in region.select("a[href^='mailto:']"):
            found += find_emails_loose(a.get("href", "").split(":", 1)[-1])
        out: list[str] = []
        for e in found:
            if e not in out:
                out.append(e)
        return out
    if "previous" in spec:
        return _previous(node, spec["previous"])
    if "attr_of_item" in spec:
        return clean_ws(node.get(spec["attr_of_item"])) or None
    region = _select(scope, spec["css"], spec.get("index", 0)) if spec.get("css") else scope
    if "labelled_link" in spec:
        if region is None:
            return None
        label = spec["labelled_link"]
        for el in region.find_all(["p", "div", "li", "span"]):
            text = _el_text(el)
            if text.startswith(label) and not el.find(["p", "div", "li"]):
                a = el.find("a", href=True)
                if a is not None and a["href"].strip().lower().startswith("http"):
                    return a["href"].strip()
                m = _URL_RE.search(text)
                return m.group(0) if m else None
        return None
    if "label" in spec:
        if region is None:
            return None
        text = "\n".join(_lines(region))
        m = re.search(spec["label"], text, re.M)
        return clean_ws(m.group(1)) or None if m else None
    if "section" in spec:
        s = spec["section"]
        return section_lines(scope, s.get("container"), s["boundary"], s["titles"], s.get("stop_titles"), int(s.get("max_lines", 12)))
    if "section_items" in spec:
        s = spec["section_items"]
        return section_items(scope, s["item"], s["title"], s["body"], s["titles"], s.get("line_css"), s.get("strip_prefix"),
                             int(s.get("max_lines", 12)))
    if region is None:
        return None
    if "attr" in spec:
        val = region.get(spec["attr"])
        return _abs(base_url, val) if spec["attr"] == "href" else clean_ws(val) or None
    return _el_text(region) or None


# ----------------------------------------------------------------------------------------------- list / profile
def classify_href(href: Optional[str], base_url: str, patterns: list[str], domains: list[str]) -> tuple[str, Optional[str]]:
    """-> (kind, absolute url). kind: personal (matches a configured person-page pattern) | external (off the university's
    domains: a personal website at best) | other (on-domain but not a person page, e.g. an article) | none."""
    if not href:
        return "none", None
    h = href.strip()
    if not h or h.startswith("#") or h.lower().startswith(("javascript:", "mailto:", "tel:")):
        return "none", None
    url = urljoin(base_url, h)
    if not url.lower().startswith(("http://", "https://")):
        return "none", None
    if any(re.search(p, url) for p in patterns):
        return "personal", url
    if domains and not host_in_domains(url, domains):
        return "external", url
    return "other", url


def parse_list(html: str, cfg: SiteConfig, base_url: Optional[str] = None) -> ListParse:
    base = base_url or cfg.list_url
    soup = soup_of(html)
    spec = cfg.list_spec
    href_re = re.compile(spec["item_href_pattern"]) if spec.get("item_href_pattern") else None
    fields = spec["fields"]
    items = soup.select(spec["item"])
    people: list[RawPerson] = []
    seen: set[str] = set()
    skipped = dup = 0
    for node in items:
        href = extract_field(node, fields["profile_href"], base) if "profile_href" in fields else None
        if href_re is not None and not (href and href_re.search(href)):
            skipped += 1
            continue
        if href:
            key = canonical_url(href)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
        emails = extract_field(node, fields["emails"], base) if "emails" in fields else []
        people.append(RawPerson(
            name_raw=extract_field(node, fields["name"], base) if "name" in fields else None,
            profile_href=href,
            position_raw=extract_field(node, fields["position"], base) if "position" in fields else None,
            emails=list(emails or []),
            department=extract_field(node, fields["department"], base) if "department" in fields else None,
            group=extract_field(node, fields["group"], base) if "group" in fields else None))
    return ListParse(people, len(items), skipped, dup, visible_text(html))


def parse_profile(html: str, cfg: SiteConfig, base_url: str) -> ProfileData:
    soup = soup_of(html)
    fields = cfg.profile_spec.get("fields") or {}
    data = ProfileData(text=visible_text(html))
    for key in ("name", "position", "department", "personal_website"):
        if key in fields:
            setattr(data, key, extract_field(soup, fields[key], base_url))
    if "emails" in fields:
        data.emails = list(extract_field(soup, fields["emails"], base_url) or [])
    if "interests" in fields:
        data.interests_lines = extract_field(soup, fields["interests"], base_url)
    return data


def llm_bio_text(html: str, cfg: SiteConfig, max_chars: int = 1500) -> Optional[str]:
    """Text handed to Groq for interests when the page has no interests section: either a CSS region (profile.llm_interests_css, SJTU bio)
    or a named section (profile.llm_interests_section, Tsinghua 研究概况)."""
    soup = soup_of(html)
    css = cfg.profile_spec.get("llm_interests_css")
    if css:
        el = soup.select_one(css)
        text = "\n".join(_lines(el)) if el is not None else ""
        return text[:max_chars] or None
    sec = cfg.profile_spec.get("llm_interests_section")
    if sec:
        lines = section_lines(soup, sec.get("container"), sec["boundary"], sec["titles"], sec.get("stop_titles"), int(sec.get("max_lines", 6)))
        return "\n".join(lines)[:max_chars] if lines else None
    return None


def census_headings(html: str, cfg: SiteConfig) -> tuple[list[str], bool]:
    """(section titles found on a profile page, whether the interests spec yields lines). Diagnostic for `extract headings`."""
    soup = soup_of(html)
    spec = (cfg.profile_spec.get("fields") or {}).get("interests") or {}
    titles: list[str] = []
    if "section" in spec:
        s = spec["section"]
        cont = soup.select_one(s["container"]) if s.get("container") else soup
        kids = [c for c in cont.children if isinstance(c, Tag)] if cont is not None else []
        for i, k in enumerate(kids):
            if sv.match(s["boundary"], k):
                h = _el_text(k) or (_el_text(kids[i + 1]) if i + 1 < len(kids) else "")
                if h:
                    titles.append(re.sub(r"[：:\s]", "", h))
    elif "section_items" in spec:
        s = spec["section_items"]
        for it in soup.select(s["item"]):
            t = it.select_one(s["title"])
            if t is not None and _el_text(t):
                titles.append(re.sub(r"[：:\s]", "", _el_text(t)))
    got = extract_field(soup, spec, "") if spec else None
    return titles, bool(got)
