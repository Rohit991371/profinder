"""Position normaliser: Chinese or English title -> (normalised English title, role class).

role class: faculty | non_faculty | unknown. 'unknown' means a human decides (doc 02: role unclear -> needs_review).
Decision pending for Iteration 4: 助理研究员 (assistant researcher) is currently classed as faculty."""
import re
from typing import Optional

_EXACT_ZH = {
    "教授": ("Professor", "faculty"), "正教授": ("Professor", "faculty"),
    "副教授": ("Associate Professor", "faculty"), "助理教授": ("Assistant Professor", "faculty"),
    "讲师": ("Lecturer", "faculty"), "研究员": ("Researcher", "faculty"),
    "副研究员": ("Associate Researcher", "faculty"), "助理研究员": ("Assistant Researcher", "faculty"),
}
_NON_FACULTY_EN = ("postdoc", "post-doc", "postdoctoral", "student", "candidate", "engineer", "technician",
                   "administrator", "secretary", "staff", "assistant to")
_UNCLEAR_EN = ("adjunct", "visiting", "emeritus", "honorary", "courtesy", "distinguished visiting")


def normalise_position(position: Optional[str]) -> tuple[Optional[str], str]:
    if not position or not position.strip():
        return None, "unknown"
    p = re.sub(r"\s+", "", position)
    if p in _EXACT_ZH:
        return _EXACT_ZH[p]
    if re.search(r"[\u3400-\u9fff]", p):
        return _normalise_zh(p)
    return _normalise_en(position)


def _normalise_zh(p: str) -> tuple[Optional[str], str]:
    if "博士后" in p:
        return "Postdoctoral Researcher", "non_faculty"
    if any(k in p for k in ("工程师", "实验师", "技术员", "工程技术")):
        return "Engineer/Technician", "non_faculty"
    for key, value in (("助理教授", "Assistant Professor"), ("副教授", "Associate Professor"), ("教授", "Professor"),
                       ("副研究员", "Associate Researcher"), ("助理研究员", "Assistant Researcher"),
                       ("研究员", "Researcher"), ("讲师", "Lecturer")):
        if key in p:
            return value, "faculty"
    return None, "unknown"


def _normalise_en(position: str) -> tuple[Optional[str], str]:
    p = re.sub(r"\s+", " ", position.strip().lower())
    if "postdoc" in p or "post-doc" in p or "postdoctoral" in p:
        return "Postdoctoral Researcher", "non_faculty"
    if any(k in p for k in _NON_FACULTY_EN):
        return None, "non_faculty"
    if any(k in p for k in _UNCLEAR_EN):
        return None, "unknown"
    for key, value in (("assistant professor", "Assistant Professor"), ("associate professor", "Associate Professor"),
                       ("professor", "Professor"), ("lecturer", "Lecturer"),
                       ("associate researcher", "Associate Researcher"), ("assistant researcher", "Assistant Researcher"),
                       ("researcher", "Researcher"), ("research fellow", "Researcher")):
        if key in p:
            return value, "faculty"
    return None, "unknown"
