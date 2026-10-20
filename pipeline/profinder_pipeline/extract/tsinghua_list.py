"""HTML -> raw faculty entries for the Tsinghua CS list page (bs4 + lxml, no pydantic).

Layout seen on the live page (via a text conversion, raw DOM not yet inspected):
  h4 = institute, h3 = rank group, then one entry per person:
  [photo link] / h2 > a (name, links to /info/<n>/<id>.htm) / position / phone / email
The parser keys on h2 > a[href*=/info/...]; if no h2 entries are found it falls back to any such link inside an <li>.
"""
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString

from ..utils.emails import find_emails
from ..utils.text import PHONE_LINE_RE
from ..utils.urls import canonical_url

INFO_RE = re.compile(r"/info/\d+/\d+\.htm", re.I)
MAILTO_RE = re.compile(r"^mailto:", re.I)


@dataclass
class RawEntry:
    name_raw: str
    profile_url: str
    position_zh: Optional[str]
    phones: list[str]
    emails: list[str]
    institute_zh: Optional[str]
    rank_group_zh: Optional[str]


@dataclass
class ParseResult:
    entries: list[RawEntry] = field(default_factory=list)
    mode: str = "h2"
    raw_entry_count: int = 0
    duplicate_urls: int = 0


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _entry_text(anchor_holder) -> tuple[list[str], list[str]]:
    """Text lines and mailto addresses belonging to one entry."""
    li = anchor_holder.find_parent("li")
    container = li if li is not None else anchor_holder.parent
    if container is not None:
        urls = {a.get("href") for a in container.find_all("a", href=INFO_RE)}
        if len(urls) <= 1:
            return list(container.stripped_strings), [a["href"][7:] for a in container.find_all("a", href=MAILTO_RE)]
    # No per-entry wrapper: take following siblings until the next entry/heading.
    lines: list[str] = []
    mailtos: list[str] = []
    for sib in anchor_holder.next_siblings:
        if isinstance(sib, NavigableString):
            s = str(sib).strip()
            if s:
                lines.append(s)
            continue
        if sib.name in ("h2", "h3", "h4", "li") or sib.find("h2") is not None:
            break
        lines.extend(sib.stripped_strings)
        mailtos.extend(a["href"][7:] for a in sib.find_all("a", href=MAILTO_RE))
    return lines, mailtos


def _build(holder, link, base_url: str) -> RawEntry:
    name_raw = link.get_text(strip=True) or (link.get("title") or "").strip()
    lines, mailtos = _entry_text(holder)
    emails = find_emails(" ".join(mailtos + lines))
    position = None
    phones: list[str] = []
    for line in lines:
        if _norm(line) == _norm(name_raw):
            continue
        if "@" in line:
            continue
        if PHONE_LINE_RE.match(line):
            phones.append(line)
            continue
        if position is None:
            position = line.strip()
    inst = holder.find_previous("h4")
    rank = holder.find_previous("h3")
    return RawEntry(
        name_raw=name_raw,
        profile_url=canonical_url(urljoin(base_url, link["href"])),
        position_zh=position,
        phones=phones,
        emails=emails,
        institute_zh=inst.get_text(strip=True) if inst else None,
        rank_group_zh=rank.get_text(strip=True) if rank else None,
    )


def parse_list_html(html: str, base_url: str) -> ParseResult:
    soup = BeautifulSoup(html, "lxml")
    raw: list[RawEntry] = []
    for h2 in soup.find_all("h2"):
        link = h2.find("a", href=INFO_RE)
        if link is not None and (link.get_text(strip=True) or link.get("title")):
            raw.append(_build(h2, link, base_url))
    mode = "h2"
    if not raw:
        mode = "fallback_li_links"
        for link in soup.find_all("a", href=INFO_RE):
            if link.get_text(strip=True) and link.find_parent("li") is not None:
                raw.append(_build(link, link, base_url))
    result = ParseResult(mode=mode, raw_entry_count=len(raw))
    seen: set[str] = set()
    for e in raw:
        if e.profile_url in seen:
            result.duplicate_urls += 1
            continue
        seen.add(e.profile_url)
        result.entries.append(e)
    return result


def visible_text(html: str) -> str:
    """Visible page text (scripts/styles removed), blank lines collapsed. Full text is kept; truncation happens only for the LLM."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
