"""URL helpers. canonical_url is the single definition used for identity keys and the page-cache key."""
import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = ("utm_", "spm", "fbclid", "gclid")
_INDEX_RE = re.compile(r"/index\.(htm|html|jsp|shtml)$", re.I)


def is_http_url(url: str) -> bool:
    return isinstance(url, str) and url.lower().startswith(("http://", "https://"))


def canonical_url(url: str) -> str:
    """Lowercase scheme+host, drop fragment and tracking params, normalise index pages and the trailing slash."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    path = _INDEX_RE.sub("/", parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path or "/", urlencode(query), ""))


def host_of(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").lower()


def host_in_domains(url_or_host: str, domains: Iterable[str]) -> bool:
    """True if the host equals a registered domain or is a sub-domain of it (evil-tsinghua.edu.cn does NOT match)."""
    host = host_of(url_or_host) if "/" in url_or_host else url_or_host.lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)
