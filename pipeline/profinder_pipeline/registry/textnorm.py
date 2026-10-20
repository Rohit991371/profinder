from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

_CJK = re.compile(r"[\u4e00-\u9fff]")
_TWO_LEVEL = {"edu.cn", "com.cn", "org.cn", "ac.cn", "gov.cn", "net.cn", "com.hk", "edu.hk", "edu.tw", "edu.mo"}
_PROV_WORDS = ("province", "municipality", "autonomous", "region", "zhuang", "uyghur", "uygur", "hui", "city")


def has_cjk(s: str | None) -> bool:
    return bool(s and _CJK.search(s))


def norm_en(s: str | None) -> str:
    """Name key: case-folded, '&'->'and', no spaces/punctuation (so 'ElectricPower' == 'Electric Power')."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).casefold().replace("&", " and ")
    s = re.sub(r"^the\s+", "", s.strip())
    return re.sub(r"[^a-z0-9]+", "", s)


def prov_key(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).casefold()
    for w in _PROV_WORDS:
        s = s.replace(w, " ")
    return re.sub(r"[^a-z]+", "", s)


def city_key(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).casefold()
    s = re.sub(r"\b(city|shi|district)\b", " ", s)
    return re.sub(r"[^a-z]+", "", s)


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    try:
        host = urlsplit(u).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def registrable_domain(host_or_url: str | None) -> str | None:
    host = host_of(host_or_url)
    if not host or re.fullmatch(r"[\d.]+", host) or ":" in host:
        return None
    parts = host.split(".")
    if len(parts) < 2:
        return None
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def origin_url(url: str | None, prefer_https: bool = False) -> str | None:
    """'http://www.buaa.edu.cn/x/y?z' -> 'http://www.buaa.edu.cn/' (or https when asked)."""
    if not url:
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    sp = urlsplit(u)
    host = (sp.hostname or "").lower().rstrip(".")
    if not host:
        return None
    scheme = "https" if prefer_https else (sp.scheme or "http").lower()
    port = f":{sp.port}" if sp.port and sp.port not in (80, 443) else ""
    return f"{scheme}://{host}{port}/"


def host_allowed(host: str | None, domains: list[str] | tuple[str, ...]) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in domains)
