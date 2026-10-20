"""Status mapping (doc 02 section 2): one pipeline decision -> BOTH app status fields, always consistent."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import DataStatus, EntityType, ProposedStatus, VerificationStatus

# Program has no dataStatus column (doc 02 notes). Publication is not loaded with statuses in v1.
ENTITIES_WITH_DATA_STATUS = {EntityType.professor, EntityType.university, EntityType.scholarship}

_MAP = {
    ProposedStatus.verified: (DataStatus.verified, VerificationStatus.VERIFIED),
    ProposedStatus.needs_review: (DataStatus.needs_review, VerificationStatus.PENDING_REVIEW),
    ProposedStatus.unverified: (DataStatus.unverified, VerificationStatus.UNVERIFIED),
    ProposedStatus.outdated: (DataStatus.outdated, VerificationStatus.POTENTIALLY_OUTDATED),
}
LEGACY_DATA_STATUS = {"needs_verification": DataStatus.needs_review}

# Timestamp columns that exist per Prisma model (verifiedAt / lastVerifiedAt are not on every model).
TIMESTAMP_COLUMNS = {
    EntityType.university: ("verifiedAt", "lastCheckedAt"),
    EntityType.professor: ("verifiedAt", "lastVerifiedAt", "lastCheckedAt"),
    EntityType.program: ("lastVerifiedAt", "lastCheckedAt"),
    EntityType.scholarship: ("lastVerifiedAt", "lastCheckedAt"),
}


@dataclass(frozen=True)
class StatusPair:
    data_status: Optional[DataStatus]          # None for entities without a dataStatus column
    verification_status: VerificationStatus


def status_pair(entity: EntityType, status: ProposedStatus) -> StatusPair:
    """The two values the loader writes for a decision. 'rejected' is not a status and is never written."""
    if not isinstance(status, ProposedStatus):
        raise ValueError(f"{status!r} is not a writable status (rejected rows are not written)")
    data, verification = _MAP[status]
    return StatusPair(data if entity in ENTITIES_WITH_DATA_STATUS else None, verification)


def normalise_data_status(value: str) -> DataStatus:
    """Read an existing row's dataStatus; the legacy alias needs_verification means needs_review."""
    if value in LEGACY_DATA_STATUS:
        return LEGACY_DATA_STATUS[value]
    return DataStatus(value)


def is_consistent(entity: EntityType, data_status: Optional[str], verification_status: Optional[str]) -> bool:
    """True if the pair (as stored) corresponds to one pipeline status. Missing values are fine (nothing to contradict)."""
    if data_status is None or verification_status is None:
        return True
    try:
        ds = normalise_data_status(str(data_status))
        vs = VerificationStatus(str(verification_status))
    except ValueError:
        return False
    return any(d == ds and v == vs for d, v in _MAP.values())


def status_timestamps(entity: EntityType, status: ProposedStatus, fetched_at: datetime, now: datetime) -> dict[str, datetime]:
    """Timestamps the loader sets: verified -> verifiedAt/lastVerifiedAt = fetch time; lastCheckedAt = now always."""
    cols = TIMESTAMP_COLUMNS[entity]
    out = {"lastCheckedAt": now}
    if status is ProposedStatus.verified:
        for c in cols:
            if c in ("verifiedAt", "lastVerifiedAt"):
                out[c] = fetched_at
    return out
