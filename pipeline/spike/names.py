"""Deterministic 'Family Given' romanisation (pypinyin + surname overrides). The LLM never touches names."""
from __future__ import annotations

from pypinyin import Style, lazy_pinyin

from .textutils import split_zh_name

# Surname readings that differ from pypinyin's default for the character.
SURNAME_OVERRIDES = {
    "单": "Shan", "曾": "Zeng", "仇": "Qiu", "解": "Xie", "区": "Ou", "覃": "Qin", "查": "Zha", "乐": "Yue",
    "朴": "Piao", "缪": "Miao", "盖": "Ge", "种": "Chong", "员": "Yun", "折": "She", "洗": "Xian",
    "尉迟": "Yuchi", "单于": "Chanyu", "万俟": "Moqi", "长孙": "Zhangsun",
}


def _py(text: str) -> str:
    return "".join(lazy_pinyin(text, style=Style.NORMAL, errors="ignore"))


def romanise(name_zh: str) -> tuple[str, str | None, str] | None:
    """-> (name 'Family Given', first_name=given or None, last_name=family) or None if not an all-CJK name."""
    parts = split_zh_name(name_zh)
    if parts is None:
        return None
    surname_zh, given_zh = parts
    family = SURNAME_OVERRIDES.get(surname_zh) or _py(surname_zh).capitalize()
    given = _py(given_zh).capitalize() if given_zh else None
    return (f"{family} {given}" if given else family), given, family
