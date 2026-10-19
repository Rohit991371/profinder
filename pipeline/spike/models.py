"""Strict Pydantic models for the spike (no dict / List[dict] fields — decision D15)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from .textutils import EMAIL_STRICT_RE


class ProfessorCandidateV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_zh: str
    name_note_zh: Optional[str]
    name: Optional[str]          # romanised "Family Given"
    first_name: Optional[str]    # given
    last_name: Optional[str]     # family
    position_zh: Optional[str]
    position: Optional[str]      # normalised English
    role_class: Literal["faculty", "non_faculty", "unknown"]
    school: str
    institute_zh: Optional[str]
    rank_group_zh: Optional[str]
    email: Optional[str]                 # only if regex + on page + university domain
    email_seen_on_page: Optional[str]    # what the page showed (kept for the comparison report)
    email_domain_ok: Optional[bool]
    profile_url: Optional[str]
    profile_is_personal: bool
    source_url: str
    fetched_at: str
    extractor: Literal["css"]
    decision: Literal["auto_accept", "needs_review", "rejected"]
    reasons: list[str]

    @field_validator("email", "email_seen_on_page")
    @classmethod
    def _email_must_match_regex(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not EMAIL_STRICT_RE.match(v):
            raise ValueError("invalid email")
        return v

    @field_validator("profile_url", "source_url")
    @classmethod
    def _url_must_be_http(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http(s)://")
        return v


class GroqInterests(BaseModel):
    """Schema sent to Groq in the smoke test. Every field required (nullable) so strict mode accepts it."""

    model_config = ConfigDict(extra="forbid")

    research_interests: Optional[str]
    evidence_quote: Optional[str]
