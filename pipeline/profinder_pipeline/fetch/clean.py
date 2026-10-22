"""Markdown cleaner (task 3.3). Full cleaned text is stored; truncation happens only for the LLM (truncate_for_llm).

Links are kept as [text](absolute-url) so profile URLs and mailto: addresses survive. U+3000 (the ideographic space
inside names such as 蒋　力) is preserved on purpose."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "canvas", "form", "template", "button", "select",
             "input", "video", "audio", "head", "link", "meta")
LAYOUT_TAGS = ("nav", "footer", "header", "aside")
# A class/id is layout noise only when the WHOLE name is one of these (optionally with a short prefix such as g-nav, and
# a trailing digit): "part_nav" is a real content block on some sites, "nav" and "g-nav2" are menus.
_NOISE_NAME = re.compile(r"^(?:[a-z]{1,2}[-_])?(?:nav|navbar|navigation|menu|breadcrumb|bread|crumbs?|footer|sidebar|topbar|"
                         r"copyright|qrcode|ewm)\d{0,2}$")
HEADINGS = {f"h{i}": "#" * i for i in range(1, 7)}
BLOCKS = frozenset({"p", "div", "section", "article", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre", "main",
                    "figure", "figcaption", "tbody", "thead", "table", "address", "body", "html"})
_WS = re.compile(r"[ \t\r\n\f\v\u00a0]+")


@dataclass
class CleanResult:
    markdown: str
    title: str
    chars_before: int           # visible characters in the page before noise removal
    chars_after: int            # characters in the markdown
    fallback: bool              # True when aggressive noise removal was undone because it removed too much


def _is_noise(attrs: dict) -> bool:
    names = attrs.get("class") or []
    names = names.split() if isinstance(names, str) else list(names)
    if attrs.get("id"):
        names.append(str(attrs["id"]))
    return any(_NOISE_NAME.match(n.lower()) for n in names)


def strip_noise(soup: BeautifulSoup, aggressive: bool = True) -> BeautifulSoup:
    for t in soup.find_all(DROP_TAGS):
        t.decompose()
    if aggressive:
        for t in soup.find_all(LAYOUT_TAGS):
            t.decompose()
        for t in list(soup.find_all(True)):
            attrs = getattr(t, "attrs", None)                 # None once an ancestor was decomposed
            if attrs and _is_noise(attrs):
                t.decompose()
    return soup


def _inline(node, base: str) -> str:
    out: list = []
    _walk(node, base, out)
    return _WS.sub(" ", "".join(out)).strip()


def _walk(node, base: str, out: list) -> None:
    for ch in node.children:
        if isinstance(ch, NavigableString):
            if type(ch).__name__ in ("Comment", "Doctype", "CData", "ProcessingInstruction"):
                continue
            out.append(_WS.sub(" ", str(ch)))
            continue
        if not isinstance(ch, Tag):
            continue
        name = ch.name
        if name == "br":
            out.append("\n")
        elif name == "a":
            text = _inline(ch, base)
            href = (ch.get("href") or "").strip()
            if href.lower().startswith("mailto:"):
                out.append(f"[{text or href[7:]}]({href})")
            elif text and href and not href.startswith("#") and not href.lower().startswith(("javascript:", "tel:")):
                out.append(f"[{text}]({urljoin(base, href)})")
            else:
                out.append(text)
        elif name in HEADINGS:
            out.append(f"\n\n{HEADINGS[name]} {_inline(ch, base)}\n\n")
        elif name == "li":
            out.append(f"\n- {_inline(ch, base)}\n")
        elif name == "tr":
            cells = [_inline(c, base) for c in ch.find_all(["td", "th"], recursive=False)]
            if cells:
                out.append("\n| " + " | ".join(cells) + " |\n")
            else:
                _walk(ch, base, out)
        elif name in BLOCKS:
            out.append("\n")
            _walk(ch, base, out)
            out.append("\n")
        else:                                                  # span, font, strong, em, img, td outside tr ...
            if name == "img" and (ch.get("alt") or "").strip():
                out.append(ch["alt"].strip())
            else:
                _walk(ch, base, out)
    return None


def _to_markdown(soup: BeautifulSoup, base: str) -> str:
    out: list = []
    root = soup.body or soup
    _walk(root, base, out)
    text = "".join(out)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    cleaned: list = []
    for ln in lines:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        if ln and cleaned and cleaned[-1] == ln:               # identical consecutive lines (nested wrappers)
            continue
        cleaned.append(ln)
    return "\n".join(cleaned).strip()


def html_to_markdown(html: str, base_url: str = "") -> CleanResult:
    soup_full = BeautifulSoup(html or "", "lxml")
    title = (soup_full.title.get_text(" ", strip=True) if soup_full.title else "")[:300]
    before = len(BeautifulSoup(html or "", "lxml").get_text(" ", strip=True))
    try:
        md = _to_markdown(strip_noise(BeautifulSoup(html or "", "lxml"), aggressive=True), base_url)
        fallback = False
        if before > 2000 and len(md) < 0.3 * before:           # safety net: noise removal ate the content
            md, fallback = _to_markdown(strip_noise(BeautifulSoup(html or "", "lxml"), aggressive=False), base_url), True
    except RecursionError:
        md, fallback = BeautifulSoup(html or "", "lxml").get_text("\n", strip=True), True
    if title:
        md = f"# {title}\n\n{md}" if md else f"# {title}"
    return CleanResult(md, title, before, len(md), fallback)


def truncate_for_llm(markdown: str, max_chars: int = 12_000) -> str:
    """Cut at a line boundary and say so. Stored text is never truncated."""
    if len(markdown) <= max_chars:
        return markdown
    cut = markdown.rfind("\n", 0, max_chars)
    cut = cut if cut > max_chars * 0.5 else max_chars
    return markdown[:cut].rstrip() + f"\n\n[... truncated: {cut} of {len(markdown)} characters shown]"
