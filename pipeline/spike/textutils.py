"""Pure helpers (stdlib only): email, URL, position, name cleaning."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EMAIL_FIND_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_STRICT_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PHONE_LINE_RE = re.compile(r"^[\d+\-\s()（）－—]+$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")

_BRACKET_AT = re.compile(r"\s*[\[\(\{（【]\s*at\s*[\]\)\}）】]\s*", re.I)
_BRACKET_DOT = re.compile(r"\s*[\[\(\{（【]\s*dot\s*[\]\)\}）】]\s*", re.I)
_WORD_AT = re.compile(r"\s+at\s+", re.I)
_WORD_DOT = re.compile(r"\s+dot\s+", re.I)


def find_emails(text: str) -> list[str]:
    """Emails found in text (lowercased, unique, in order). De-obfuscates [at]/(at)/ AT only if nothing is found directly."""
    found = _unique(m.lower() for m in EMAIL_FIND_RE.findall(text))
    if found:
        return found
    t = _BRACKET_AT.sub("@", text)
    t = _BRACKET_DOT.sub(".", t)
    t = _WORD_AT.sub("@", t)
    t = _WORD_DOT.sub(".", t)
    return _unique(m.lower() for m in EMAIL_FIND_RE.findall(t))


def _unique(items) -> list[str]:
    seen: list[str] = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return seen


def email_domain_ok(email: str, allowed_domains: list[str]) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in allowed_domains)


_TRACKING = ("utm_", "spm", "fbclid", "gclid")


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragment and tracking params, normalise index pages and trailing slash."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    path = re.sub(r"/index\.(htm|html|jsp|shtml)$", "/", parts.path, flags=re.I)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path or "/", urlencode(query), ""))


# (english, role_class). Exact matches first, then keyword rules.
_POSITION_EXACT = {
    "教授": ("Professor", "faculty"),
    "正教授": ("Professor", "faculty"),
    "副教授": ("Associate Professor", "faculty"),
    "助理教授": ("Assistant Professor", "faculty"),
    "讲师": ("Lecturer", "faculty"),
    "研究员": ("Researcher", "faculty"),
    "副研究员": ("Associate Researcher", "faculty"),
    "助理研究员": ("Assistant Researcher", "faculty"),
}


def normalise_position(position_zh: str | None) -> tuple[str | None, str]:
    """-> (english position or None, role_class in faculty|non_faculty|unknown)."""
    if not position_zh:
        return None, "unknown"
    p = re.sub(r"\s+", "", position_zh)
    if p in _POSITION_EXACT:
        return _POSITION_EXACT[p]
    if "博士后" in p:
        return "Postdoctoral Researcher", "non_faculty"
    if "工程师" in p or "实验师" in p or "技术员" in p or "工程技术" in p:
        return "Engineer/Technician", "non_faculty"
    if "助理教授" in p:
        return "Assistant Professor", "faculty"
    if "副教授" in p:
        return "Associate Professor", "faculty"
    if "教授" in p:
        return "Professor", "faculty"
    if "副研究员" in p:
        return "Associate Researcher", "faculty"
    if "助理研究员" in p:
        return "Assistant Researcher", "faculty"
    if "研究员" in p:
        return "Researcher", "faculty"
    if "讲师" in p:
        return "Lecturer", "faculty"
    return None, "unknown"


_ANNOTATION_RE = re.compile(r"[（(]([^）)]*)[）)]")


def clean_name(raw: str) -> tuple[str, str | None]:
    """Strip '（校内兼职）'-style annotations; remove spaces inside Chinese names ('尹 霞' -> '尹霞'). -> (name, note)."""
    notes = _ANNOTATION_RE.findall(raw)
    name = _ANNOTATION_RE.sub("", raw).strip()
    if CJK_RE.search(name):
        name = re.sub(r"\s+", "", name)
    else:
        name = re.sub(r"\s+", " ", name)
    return name, ("；".join(n.strip() for n in notes if n.strip()) or None)


COMPOUND_SURNAMES = [
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "令狐", "长孙", "夏侯",
    "司徒", "轩辕", "独孤", "宇文", "端木", "南宫", "闻人", "澹台", "万俟", "申屠", "淳于", "单于", "鲜于", "第五",
]


def split_zh_name(name_zh: str) -> tuple[str, str] | None:
    """-> (surname, given) for an all-CJK name, else None. Given may be '' for one-character names."""
    if not name_zh or not all(CJK_RE.match(c) for c in name_zh):
        return None
    for s in COMPOUND_SURNAMES:
        if name_zh.startswith(s) and len(name_zh) > len(s):
            return s, name_zh[len(s):]
    return name_zh[0], name_zh[1:]
