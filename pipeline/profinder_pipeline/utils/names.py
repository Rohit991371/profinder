"""Deterministic 'Family Given' romanisation (pypinyin + surname overrides). The LLM never touches names."""
from typing import Optional

from pypinyin import Style, lazy_pinyin

from .text import split_zh_name

# Surname readings that differ from pypinyin's default for the character.
SURNAME_OVERRIDES = {
    "单": "Shan", "曾": "Zeng", "仇": "Qiu", "解": "Xie", "区": "Ou", "覃": "Qin", "查": "Zha", "乐": "Yue",
    "朴": "Piao", "缪": "Miao", "盖": "Ge", "种": "Chong", "员": "Yun", "折": "She", "洗": "Xian",
    "尉迟": "Yuchi", "单于": "Chanyu", "万俟": "Moqi", "长孙": "Zhangsun",
    "翟": "Zhai", "祭": "Zhai", "宓": "Fu", "句": "Gou", "秘": "Bi", "繁": "Po", "隗": "Wei", "訾": "Zi", "郇": "Huan",
    "阚": "Kan", "谌": "Chen", "召": "Shao", "莘": "Shen", "都": "Du", "薄": "Bo",
}


def _py(text: str) -> str:
    return "".join(lazy_pinyin(text, style=Style.NORMAL, errors="ignore"))


def romanise(name_zh: str) -> Optional[tuple[str, Optional[str], str]]:
    """-> (name 'Family Given', first_name=given or None, last_name=family), or None if not an all-CJK name."""
    parts = split_zh_name(name_zh)
    if parts is None:
        return None
    surname_zh, given_zh = parts
    family = SURNAME_OVERRIDES.get(surname_zh) or _py(surname_zh).capitalize()
    given = _py(given_zh).capitalize() if given_zh else None
    return (f"{family} {given}" if given else family), given, family
