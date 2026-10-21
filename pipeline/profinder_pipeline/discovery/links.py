from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

_SKIP_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif",
             ".svg", ".mp4", ".mp3", ".css", ".js", ".ico")


@dataclass(frozen=True)
class Link:
    url: str
    text: str


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._href: str | None = None
        self._title = ""
        self._buf: list[str] = []

    def _close(self) -> None:
        if self._href is not None:
            text = " ".join("".join(self._buf).split()) or self._title
            self.items.append((self._href, text))
        self._href, self._title, self._buf = None, "", []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self._close()
            self._href = a.get("href") or ""
            self._title = " ".join((a.get("title") or "").split())
        elif tag == "img" and self._href is not None and a.get("alt"):
            self._buf.append(" " + a["alt"] + " ")

    def handle_endtag(self, tag):
        if tag == "a":
            self._close()

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def close(self):
        super().close()
        self._close()


def clean_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def extract_links(markup: str, base_url: str) -> list[Link]:
    """All http(s) links of a page, absolute, fragment-free, de-duplicated (longest anchor text wins). No domain filtering here."""
    c = _Collector()
    try:
        c.feed(markup)
        c.close()
    except Exception:  # broken markup must not stop discovery
        pass
    best: dict[str, str] = {}
    for href, text in c.items:
        h = (href or "").strip()
        if not h or h.startswith("#") or h.lower().startswith(("javascript:", "mailto:", "tel:", "data:")):
            continue
        url = clean_url(urljoin(base_url, h))
        sp = urlsplit(url)
        if sp.scheme not in ("http", "https") or not sp.hostname or sp.path.lower().endswith(_SKIP_EXT):
            continue
        if len(text) > len(best.get(url, "")) or url not in best:
            best[url] = text
    return [Link(u, t) for u, t in best.items()]


def visible_text(markup: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", markup)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return " ".join(_html.unescape(s).split())
