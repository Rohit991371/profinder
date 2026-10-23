"""Decision engine (task 4.7): checks -> (decision, proposed_status, reject_reason). Implements doc 02 section 4.1 'Outcome table'.

  non-faculty role                         -> rejected (reason non_faculty_role)
  B1 fails (page not HTTP 200 / not allow-listed host) -> rejected (the source is not usable)
  role unknown, any other blocking check fails, or any soft flag (S_*) fails -> needs_review
  everything passes                        -> auto_accept / verified
Field-dropping checks (F_*) never change the decision: the field is already null."""
from typing import Optional, Protocol

from ..schemas.enums import Decision, ProposedStatus

REJECTED_STATUS = ProposedStatus.unverified


class CheckLike(Protocol):
    name: str
    passed: bool
    blocking: bool
    detail: Optional[str]


def decide(role_class: str, role_detail: Optional[str], checks: list) -> tuple[Decision, ProposedStatus, Optional[str]]:
    failed = [c for c in checks if not c.passed]
    if role_class == "non_faculty":
        return Decision.rejected, REJECTED_STATUS, f"non_faculty_role:{role_detail or ''}"
    if any(c.name.startswith("B1") for c in failed):
        return Decision.rejected, REJECTED_STATUS, "B1_source_not_usable:" + ";".join(c.detail or "" for c in failed if c.name.startswith("B1"))
    if role_class == "unknown":
        return Decision.needs_review, ProposedStatus.needs_review, None
    if any(c.blocking for c in failed):
        return Decision.needs_review, ProposedStatus.needs_review, None
    if any(c.name.startswith("S_") for c in failed):
        return Decision.needs_review, ProposedStatus.needs_review, None
    return Decision.auto_accept, ProposedStatus.verified, None
