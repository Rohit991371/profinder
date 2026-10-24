"""Position normaliser: Chinese or English title -> (normalised English title, role class).

role class: faculty | non_faculty | unknown. 'unknown' means a human decides (doc 02: role unclear -> needs_review).
Decision pending for Iteration 4: 助理研究员 (assistant researcher) is currently classed as faculty."""
import re
from dataclasses import dataclass
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
    if any(k in p for k in ("工程师", "高工", "实验师", "技术员", "工程技术")):
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


_SEG_RE = re.compile(r"[；;，,、/]")


def normalise_primary(position: Optional[str]) -> tuple[Optional[str], str]:
    """For title lists such as '青年研究员；上海人工智能实验室副研究员；博导': the FIRST segment that is a known role wins.
    Honours and administrative posts (博士生导师, 系主任, 教师, 国家杰青) are 'unknown' and skipped."""
    if not position or not position.strip():
        return None, "unknown"
    for seg in (s.strip() for s in _SEG_RE.split(position)):
        if seg:
            en, role = normalise_position(seg)
            if role != "unknown":
                return en, role
    return None, "unknown"


@dataclass(frozen=True)
class PositionResolution:
    raw: Optional[str]
    en: Optional[str]
    role: str                      # faculty | non_faculty | unknown
    conflict: Optional[str] = None  # set only when BOTH sources name a faculty rank and the ranks differ


def resolve_position(list_raw: Optional[str], profile_raw: Optional[str]) -> PositionResolution:
    """Iteration 5 rule. The profile page wins only when it names a known role; otherwise the list value is used (ECNU profiles often
    show honours instead of a rank). If either side says non-faculty the person is non-faculty (-> rejected). Two different
    faculty ranks (副教授 vs 副研究员) stay a soft conflict for a human; the profile rank is kept."""
    l_en, l_role = normalise_primary(list_raw)
    p_en, p_role = normalise_primary(profile_raw)
    if "non_faculty" in (l_role, p_role):
        if p_role == "non_faculty":
            return PositionResolution(profile_raw, p_en, "non_faculty")
        return PositionResolution(list_raw, l_en, "non_faculty")
    if p_role == "faculty":
        conflict = None
        if l_role == "faculty" and l_en != p_en:
            conflict = f"list '{list_raw}' vs profile '{profile_raw}'"
        return PositionResolution(profile_raw, p_en, "faculty", conflict)
    if l_role == "faculty":
        return PositionResolution(list_raw, l_en, "faculty")
    return PositionResolution(list_raw or profile_raw, None, "unknown")
