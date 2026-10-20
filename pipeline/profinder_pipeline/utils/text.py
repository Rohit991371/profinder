"""Stdlib-only text helpers: CJK detection, name cleaning, key normalisation."""
import re
import unicodedata
from typing import Optional

CJK_RE = re.compile(r"[\u3400-\u9fff]")
PHONE_LINE_RE = re.compile(r"^[\d+\-\s()（）－—]+$")
_ANNOTATION_RE = re.compile(r"[（(]([^）)]*)[）)]")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)

COMPOUND_SURNAMES = [
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "令狐", "长孙", "夏侯",
    "司徒", "轩辕", "独孤", "宇文", "端木", "南宫", "闻人", "澹台", "万俟", "申屠", "淳于", "单于", "鲜于", "第五",
]


def has_cjk(text: Optional[str]) -> bool:
    return bool(text and CJK_RE.search(text))


def is_cjk_only(text: Optional[str]) -> bool:
    """True if the text has at least one CJK character and no Latin letters (digits/punctuation ignored)."""
    if not text or not CJK_RE.search(text):
        return False
    return re.search(r"[A-Za-z]", text) is None


def normalise_key_text(text: Optional[str]) -> str:
    """NFKC + casefold + drop spaces/punctuation. KEEPS CJK (the app's normalizeName strips it, which is the bug we avoid)."""
    if not text:
        return ""
    return _NON_WORD_RE.sub("", unicodedata.normalize("NFKC", text).casefold())


def clean_name(raw: str) -> tuple[str, Optional[str]]:
    """Strip '(...)' annotations, remove spaces inside Chinese names. -> (name, annotation note or None)."""
    notes = _ANNOTATION_RE.findall(raw)
    name = _ANNOTATION_RE.sub("", raw).strip()
    if CJK_RE.search(name):
        name = re.sub(r"\s+", "", name)
    else:
        name = re.sub(r"\s+", " ", name)
    return name, ("；".join(n.strip() for n in notes if n.strip()) or None)


def split_zh_name(name_zh: str) -> Optional[tuple[str, str]]:
    """-> (surname, given) for an all-CJK name, else None. Given may be '' for one-character names."""
    if not name_zh or not all(CJK_RE.match(c) for c in name_zh):
        return None
    for s in COMPOUND_SURNAMES:
        if name_zh.startswith(s) and len(name_zh) > len(s):
            return s, name_zh[len(s):]
    return name_zh[0], name_zh[1:]
