from __future__ import annotations

import re

from .links import Link, visible_text

_NAME_ZH = re.compile(r"^[\u4e00-\u9fff·]{2,4}$")
_NAME_EN = re.compile(r"^(?:Prof\.?\s+|Dr\.?\s+)?[A-Z][a-z]+(?:[ -][A-Z][a-z]+){1,2}$")
_TITLES = re.compile(r"教授|副教授|讲师|研究员|助理研究员|Professor|Lecturer|Researcher", re.I)
_ADM_A = ("admission", "apply", "application", "招生", "报名", "申请")
_ADM_B = ("international", "foreign", "overseas", "留学生", "国际学生", "来华")


def looks_like_faculty_list(markup: str, links: list[Link]) -> bool:
    """Many person-name anchors plus academic titles. JS-rendered directories fail this on purpose (renderer = Iteration 3)."""
    names = sum(1 for l in links if _NAME_ZH.match(l.text) or _NAME_EN.match(l.text))
    titles = len(_TITLES.findall(visible_text(markup)))
    return (names >= 10 and titles >= 3) or names >= 20


def looks_like_admissions(markup: str) -> bool:
    t = visible_text(markup).lower()
    return len(t) > 300 and any(k in t for k in _ADM_A) and any(k in t for k in _ADM_B)


_SCH_INTL = ("international", "foreign", "overseas", "csc", "chinese government", "留学生", "国际", "来华", "外国")
_CS_WORDS = ("computer", "计算机", "software", "软件", "information", "信息", "artificial intelligence", "人工智能", "cyber", "网络空间", "数据", "intelligent")
_PERSONAL_TITLE = re.compile(r"home\s?page|个人主页|个人网页|personal (web)?page|\|\s*profile\b|\bprofile\s*\|", re.I)


def looks_like_scholarship(markup: str) -> bool:
    t = visible_text(markup).lower()
    return t.count("scholarship") + t.count("奖学金") >= 2 and any(k in t for k in _SCH_INTL)


def page_title(markup: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", markup)
    return " ".join(visible_text(m.group(1)).split()) if m else ""


def is_personal_page(url: str, title: str) -> bool:
    """Professors' own homepages (e.g. web.xidian.edu.cn/<name>/en/...) must never be taken for a university-level page."""
    host = (url.split("//", 1)[-1].split("/", 1)[0]).lower()
    return bool(_PERSONAL_TITLE.search(title or "")) or "/~" in url or host.startswith(("web.", "homepage.", "people."))


def cs_related(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _CS_WORDS)


def classify(kind: str, markup: str, links: list[Link]) -> bool:
    if kind == "faculty_list":
        return looks_like_faculty_list(markup, links)
    if kind == "admissions":
        return looks_like_admissions(markup)
    if kind == "scholarship":
        return looks_like_scholarship(markup)
    raise ValueError(kind)
