"""Professor validation (task 4.4), pure Python: raw extracted person + page evidence -> checked values.
No pydantic here, so it is unit-testable anywhere; validate/professor.py wraps the result into ProfessorRecord/Candidate.

Blocking checks  B1 official page HTTP 200 on an allow-listed host | B2 name in page text | B3 faculty role | B4 school from site | B5 no placeholders
Field-dropping   F_email_* , F_profile_url_200, F_interests_grounded  (the field becomes None; the record can still be verified)
Soft flags       S_*  (a failed flag sends the record to needs_review)"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from ..extract.css import ProfileData, RawPerson, classify_href
from ..extract.emailtools import email_grounded, valid_unique
from ..extract.sitecfg import SiteConfig
from ..utils.emails import email_domain_ok
from ..utils.positions import normalise_position
from ..utils.text import CJK_RE, clean_name
from ..utils.urls import canonical_url, host_in_domains
from .areas import classify_areas
from .grounding import join_interests, keywords_from, lines_grounded, norm_text, text_in


@dataclass
class Check:
    name: str
    passed: bool
    blocking: bool = True
    detail: Optional[str] = None


@dataclass
class SourcePage:
    """A fetched page used as evidence."""
    url: str
    status: Optional[int]
    fetched_at: datetime
    text: str
    page_id: Optional[str] = None
    host_ok: bool = True

    def __post_init__(self) -> None:
        self.text_norm = norm_text(self.text)


@dataclass
class Context:
    university: str
    site: SiteConfig
    domains: list[str]
    email_domains: list[str]
    list_page: SourcePage
    suppress_emails: set[str] = field(default_factory=set)
    suppress_names: set[str] = field(default_factory=set)
    university_flags: list[Check] = field(default_factory=list)      # S_list_count_drift, S_list_below_min, ...
    llm_new_layout: bool = False


@dataclass
class Built:
    name_zh: str
    name: str
    first_name: Optional[str]
    last_name: Optional[str]
    position_en: Optional[str]
    position_raw: Optional[str]
    role_class: str
    school: Optional[str]
    department: Optional[str]
    email: Optional[str]
    profile_url: Optional[str]
    personal: bool
    personal_website: Optional[str]
    interests: Optional[str]
    interests_from_llm: bool
    keywords: Optional[str]
    areas: list[str]
    checks: list[Check]
    evidence: list[tuple[str, str]]
    source_url: str
    page_id: Optional[str]
    fetched_at: datetime
    confidence: float
    extractor: str                 # css | llm


def _unusual_name(name_zh: str) -> Optional[str]:
    if not CJK_RE.search(name_zh):
        return "not a Chinese-script name (kept as on the page)"
    if not all(CJK_RE.match(c) for c in name_zh) or not (2 <= len(name_zh) <= 4):
        return "unusual name format"
    return None


def evaluate_person(raw: RawPerson, profile: Optional[ProfileData], profile_page: Optional[SourcePage], ctx: Context,
                    llm_interests: Optional[str] = None, romanise_fn: Optional[Callable] = None,
                    extractor: str = "css") -> Optional[Built]:
    """Returns None when there is no usable name at all (nothing to build a record from)."""
    if romanise_fn is None:
        from ..utils.names import romanise as romanise_fn          # lazy: pypinyin
    cfg = ctx.site
    name_src = raw.name_raw or (profile.name if profile else None) or ""
    name_zh, note = clean_name(name_src)
    if not name_zh:
        return None
    checks: list[Check] = []
    ev: list[tuple[str, str]] = []

    # ---- source page(s): the profile page is the source when the site requires it, else the list page
    source = profile_page if (cfg.profile_required and profile_page is not None) else ctx.list_page
    pages = [ctx.list_page] + ([profile_page] if profile_page is not None else [])

    # ---- role (profile wins over list; a disagreement is a soft flag)
    pos_raw = (profile.position if profile and profile.position else None) or raw.position_raw
    pos_en, role = normalise_position(pos_raw)
    if profile and profile.position and raw.position_raw:
        other_en, _ = normalise_position(raw.position_raw)
        if other_en != normalise_position(profile.position)[0]:
            checks.append(Check("S_position_conflict", False, False, f"list '{raw.position_raw}' vs profile '{profile.position}'"))
    if pos_raw:
        ev.append(("position_raw", pos_raw))

    # ---- names
    rom = romanise_fn(name_zh)
    name = rom[0] if rom else name_zh
    first, last = (rom[1], rom[2]) if rom else (None, None)
    if profile and profile.name:
        pname, _ = clean_name(profile.name)
        if pname and norm_text(pname) != norm_text(name_zh):
            checks.append(Check("S_name_mismatch", False, False, f"list '{name_zh}' vs profile '{pname}'"))
    unusual = _unusual_name(name_zh)
    checks.append(Check("S_unusual_name", unusual is None, False, unusual))
    checks.append(Check("S_concurrent_post", note is None, False, note))
    if note:
        ev.append(("name_note", note))
    suppressed_name = name_zh in ctx.suppress_names

    # ---- B1..B4
    b1_ok = (ctx.list_page.status == 200 and ctx.list_page.host_ok and
             (not cfg.profile_required or (profile_page is not None and profile_page.status == 200 and profile_page.host_ok)))
    checks.append(Check("B1_official_page_200", b1_ok, True,
                        f"list HTTP {ctx.list_page.status}" + (f", profile HTTP {profile_page.status}" if profile_page else "")))
    checks.append(Check("B2_name_in_page_text", text_in(name_zh, source.text_norm), True, name_zh))
    checks.append(Check("B3_faculty_role", role == "faculty", True, pos_raw))
    school = cfg.school_en or cfg.school_zh
    dept = raw.department or (profile.department if profile else None)
    checks.append(Check("B4_school_from_page", bool(school), True, "school/department name from the site config (page title)"))
    if dept:
        ev.append(("department", dept))
    if raw.group:
        ev.append(("group", raw.group))

    # ---- email (field-dropping)
    found = valid_unique(list(raw.emails) + (list(profile.emails) if profile else []))
    if len(found) > 1:
        checks.append(Check("S_single_email", False, False, f"{len(found)} different emails on the page(s)"))
    email: Optional[str] = None
    if found:
        seen = found[0]
        ev.append(("email_seen_on_page", seen))
        grounded = any(email_grounded(seen, p.text) for p in pages)
        domain_ok = email_domain_ok(seen, ctx.email_domains)
        blocked = seen in ctx.suppress_emails
        checks.append(Check("F_email_grounded", grounded, False, None))
        checks.append(Check("F_email_domain", domain_ok, False, seen.rsplit("@", 1)[-1]))
        if blocked:
            checks.append(Check("F_email_suppressed", False, False, "on suppress list"))
        email = seen if (grounded and domain_ok and not blocked) else None
    if suppressed_name:
        checks.append(Check("F_name_suppressed", False, True, "on suppress list"))

    # ---- profile URL (field-dropping): only per-person pages on an allowed domain, never the list itself
    kind, url = classify_href(raw.profile_href, cfg.list_url, cfg.personal_url_patterns, ctx.domains)
    profile_url: Optional[str] = None
    personal = False
    website: Optional[str] = None
    if kind == "personal" and url and host_in_domains(url, ctx.domains) and canonical_url(url) != canonical_url(cfg.list_url):
        if profile_page is not None and profile_page.status != 200:
            checks.append(Check("F_profile_url_200", False, False, f"HTTP {profile_page.status}"))
        else:
            profile_url, personal = url, True
            checks.append(Check("F_profile_url_200", True, False, "HTTP 200" if profile_page else "profile page not fetched yet (not re-verified)"))
    elif kind == "external" and url:
        website = url
    if profile and profile.personal_website and profile.personal_website.lower().startswith(("http://", "https://")):
        website = profile.personal_website

    # ---- interests (CSS lines, else an LLM result that was already grounded)
    interests: Optional[str] = None
    from_llm = False
    if profile and profile.interests_lines:
        if profile_page is not None and lines_grounded(profile.interests_lines, profile_page.text_norm):
            interests = join_interests(profile.interests_lines)
            checks.append(Check("F_interests_grounded", True, False, None))
        else:
            checks.append(Check("F_interests_grounded", False, False, "interest text not found in the page text"))
    if interests is None and llm_interests:
        interests, from_llm = llm_interests, True
    if interests:
        ev.append(("researchInterests" + ("(llm)" if from_llm else ""), interests[:480]))
    keywords = keywords_from(interests)
    hits = classify_areas(interests or "")
    for h in hits:
        ev.append((f"area:{h.area}", ", ".join(h.terms)))

    # ---- university-level flags and new-layout flag
    checks.extend(ctx.university_flags)
    if ctx.llm_new_layout:
        checks.append(Check("S_llm_new_layout", False, False, "extracted by the LLM on a layout without a site config"))

    soft_failed = sum(1 for c in checks if c.name.startswith("S_") and not c.passed)
    conf = 0.95 - 0.10 * soft_failed - (0.05 if email is None else 0.0) - (0.05 if from_llm else 0.0)
    if extractor == "llm":
        conf = min(conf, 0.70)
    return Built(
        name_zh=name_zh, name=name, first_name=first, last_name=last, position_en=pos_en, position_raw=pos_raw, role_class=role,
        school=school, department=dept, email=email, profile_url=profile_url, personal=personal, personal_website=website,
        interests=interests, interests_from_llm=from_llm, keywords=keywords, areas=[h.area for h in hits], checks=checks,
        evidence=ev, source_url=source.url, page_id=source.page_id, fetched_at=source.fetched_at,
        confidence=round(max(0.3, min(0.99, conf)), 2), extractor=extractor)
