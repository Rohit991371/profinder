"""Write-mode engine (task 5.2, doc 01 section 8.4). Pure Python: (candidate payload, existing row) -> Plan.

insert   no existing row                                   -> new row (payload as is)
enrich   existing field is NULL / empty / known placeholder -> fill that field only
propose  existing field holds a different real value        -> NO write; a ChangeLog proposal is made later
noop     nothing to do
Also: a status is only ever RAISED (unverified < needs_review < verified), only for auto-accepted / human-approved candidates and
never on a human-touched row. A human-touched row (see is_human_touched) only receives proposals. There is no delete mode.
`insert-only` mode leaves every existing row untouched (action noop, no proposals)."""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..schemas.enums import EntityType, ProposedStatus
from ..schemas.status import normalise_data_status, status_pair
from ..utils.placeholders import PlaceholderRegistry
from ..utils.text import has_cjk
from ..utils.urls import canonical_url

URL_FIELDS = frozenset({"officialUrl", "sourceUrl", "applicationUrl", "scholarshipUrl", "profileUrl", "personalWebsite", "programUrl", "logoUrl"})
# Never diffed on an existing row. name/country/dataSource/universityId are only written on insert; statuses, timestamps and
# confidence travel with the status upgrade (the loader sets timestamps); researchAreas is a link table handled separately.
META_FIELDS = frozenset({"name", "country", "dataSource", "universityId", "dataStatus", "verificationStatus", "verifiedAt",
                         "lastVerifiedAt", "lastCheckedAt", "lastSyncedAt", "confidence", "researchAreas", "id", "createdAt", "updatedAt"})
# Free-text fields where a Chinese-only new value is NOT comparable with an existing English one (the page only has Chinese).
LANGUAGE_FIELDS = frozenset({"department", "school", "lab"})
IDENTITY_FIELDS = {"Program": frozenset({"degree", "major"}), "Scholarship": frozenset({"name"})}
ENTITY_BY_MODEL = {"University": EntityType.university, "Professor": EntityType.professor,
                   "Program": EntityType.program, "Scholarship": EntityType.scholarship}
_RANK_DATA = {"unverified": 0, "outdated": 0, "needs_review": 1, "verified": 2}
_RANK_VER = {"UNVERIFIED": 0, "POTENTIALLY_OUTDATED": 0, "PENDING_REVIEW": 1, "VERIFIED": 2}
WRITABLE_DECISIONS = frozenset({"auto_accept", "human_approved"})
SCRIPT_SOURCES = frozenset({"official_import", "openalex", "seed"})   # rows made by scripts, never by a person


@dataclass(frozen=True)
class Change:
    field: str
    before: Any
    after: Any
    kind: str            # enrich | propose | status

    def as_json(self) -> dict[str, Any]:
        enc = lambda v: None if v is None else json.dumps(v, ensure_ascii=False, default=str)  # noqa: E731
        return {"field": self.field, "before": enc(self.before), "after": enc(self.after), "kind": self.kind}


@dataclass
class Plan:
    action: str                                  # insert | enrich | propose | noop
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def of_kind(self, *kinds: str) -> list[Change]:
        return [c for c in self.changes if c.kind in kinds]


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def _norm(fld: str, v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    s = str(v)
    if fld in URL_FIELDS:                      # http/https and a leading www. are the same site for comparison purposes
        try:
            c = canonical_url(s)
        except Exception:  # noqa: BLE001
            c = s.strip()
        return re.sub(r"^https?://(www\.)?", "", c, flags=re.I)
    if fld == "email":
        return s.strip().lower()
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip().casefold()


def _aware(d: Optional[datetime]) -> Optional[datetime]:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_human_touched(created_at: Optional[datetime], updated_at: Optional[datetime], last_synced_at: Optional[datetime],
                     slack: timedelta = timedelta(seconds=60)) -> bool:
    """doc 02 section 7: `updatedAt` later than the pipeline's own `lastSyncedAt` by more than a minute = a person edited the row.
    A row the pipeline never synced is compared with its creation time (an untouched legacy row has updatedAt ~ createdAt)."""
    upd, ref = _aware(updated_at), _aware(last_synced_at) or _aware(created_at)
    if upd is None or ref is None:
        return False
    return (upd - ref) > slack


def shared_profile_urls(rows: list, min_people: int = 2) -> frozenset:
    """Normalised profileUrls that several existing professors share = a directory page, not a personal profile (treated as empty)."""
    from collections import Counter
    counts = Counter(_norm("profileUrl", r.get("profileUrl")) for r in rows if isinstance(r.get("profileUrl"), str) and r.get("profileUrl").strip())
    return frozenset(u for u, n in counts.items() if n >= min_people)


def status_rank(data_status: Optional[str], verification_status: Optional[str]) -> int:
    ranks: list[int] = []
    if data_status:
        try:
            ranks.append(_RANK_DATA[normalise_data_status(str(data_status)).value])
        except (ValueError, KeyError):
            pass
    if verification_status:
        ranks.append(_RANK_VER.get(str(verification_status), 0))
    return max(ranks) if ranks else 0


def plan_record(entity: str, payload: dict[str, Any], existing: Optional[dict[str, Any]], *, registry: PlaceholderRegistry,
                mode: str = "additive", proposed_status: Optional[str] = None, status_writable: bool = False,
                locked: bool = False, existing_areas: Optional[set[str]] = None,
                shared_urls: frozenset = frozenset()) -> Plan:
    """`entity` is the Prisma model name ('Professor' ...). `status_writable` = the candidate is auto_accept / human_approved."""
    if existing is None:
        return Plan("insert")
    if mode == "insert-only":
        return Plan("noop", [], ["insert-only: existing row left untouched"])
    changes: list[Change] = []
    skip = META_FIELDS | IDENTITY_FIELDS.get(entity, frozenset())
    kind_fill = "propose" if locked else "enrich"
    for f, v in payload.items():
        if f in skip or f == "profileIsPersonal" or _empty(v):
            continue
        cur = existing.get(f)
        if f in LANGUAGE_FIELDS and isinstance(v, str) and isinstance(cur, str) and cur.strip() and has_cjk(v) and not has_cjk(cur):
            continue                           # existing English value vs Chinese page text: not a conflict, not an improvement
        shared = f == "profileUrl" and isinstance(cur, str) and _norm(f, cur) in shared_urls   # a directory URL used by several people
        if registry.is_fillable(entity, f, cur) or shared:
            changes.append(Change(f, cur, v, kind_fill))
        elif _norm(f, cur) != _norm(f, v):
            changes.append(Change(f, cur, v, "propose"))
    if payload.get("profileIsPersonal") is True and not existing.get("profileIsPersonal") and payload.get("profileUrl"):
        same_url = _norm("profileUrl", existing.get("profileUrl")) == _norm("profileUrl", payload.get("profileUrl"))
        if same_url or any(c.field == "profileUrl" and c.kind == "enrich" for c in changes):
            changes.append(Change("profileIsPersonal", bool(existing.get("profileIsPersonal")), True, kind_fill))

    areas = [a for a in (payload.get("researchAreas") or []) if isinstance(a, str)]
    if areas and existing_areas is not None:
        missing = [a for a in areas if a not in existing_areas]
        if missing:
            changes.append(Change("researchAreas", sorted(existing_areas), sorted(existing_areas | set(missing)), kind_fill))

    if status_writable and proposed_status:
        entity_type = ENTITY_BY_MODEL[entity]
        if status_rank(existing.get("dataStatus"), existing.get("verificationStatus")) < _RANK_DATA[proposed_status]:
            pair = status_pair(entity_type, ProposedStatus(proposed_status))
            kind_status = "propose" if locked else "status"
            if pair.data_status is not None and "dataStatus" in existing:
                changes.append(Change("dataStatus", existing.get("dataStatus"), pair.data_status.value, kind_status))
            changes.append(Change("verificationStatus", existing.get("verificationStatus"), pair.verification_status.value, kind_status))

    notes = ["human-touched row: proposals only"] if locked else []
    if any(c.kind in ("enrich", "status") for c in changes):
        return Plan("enrich", changes, notes)
    if changes:
        return Plan("propose", changes, notes)
    return Plan("noop", [], notes)


def apply_plan(existing: Optional[dict[str, Any]], plan: Plan, payload: dict[str, Any]) -> dict[str, Any]:
    """What the loader does with a plan (used by the idempotency tests): insert -> the payload; enrich/status -> fields; propose -> nothing."""
    if plan.action == "insert" or existing is None:
        return dict(payload)
    row = dict(existing)
    for c in plan.of_kind("enrich", "status"):
        row[c.field] = list(c.after) if c.field == "researchAreas" else c.after
    return row


def effective_decision(decision: str, checks: list[dict[str, Any]]) -> str:
    """A matching problem (M_* blocking check failed) turns an auto-accepted candidate into a review item without rewriting `decision`."""
    if decision == "auto_accept" and any(c.get("name", "").startswith("M_") and not c.get("passed", True) and c.get("blocking", True)
                                         for c in checks):
        return "needs_review"
    return decision
