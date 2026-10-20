from datetime import datetime, timezone

import pytest

from profinder_pipeline.schemas.enums import DataStatus, EntityType, ProposedStatus, VerificationStatus
from profinder_pipeline.schemas.status import (is_consistent, normalise_data_status, status_pair, status_timestamps)

E = EntityType


def test_status_table_from_doc_02():
    p = status_pair(E.professor, ProposedStatus.verified)
    assert (p.data_status, p.verification_status) == (DataStatus.verified, VerificationStatus.VERIFIED)
    p = status_pair(E.university, ProposedStatus.needs_review)
    assert (p.data_status, p.verification_status) == (DataStatus.needs_review, VerificationStatus.PENDING_REVIEW)
    p = status_pair(E.scholarship, ProposedStatus.unverified)
    assert (p.data_status, p.verification_status) == (DataStatus.unverified, VerificationStatus.UNVERIFIED)
    p = status_pair(E.professor, ProposedStatus.outdated)
    assert (p.data_status, p.verification_status) == (DataStatus.outdated, VerificationStatus.POTENTIALLY_OUTDATED)


def test_program_has_no_data_status():
    p = status_pair(E.program, ProposedStatus.verified)
    assert p.data_status is None and p.verification_status == VerificationStatus.VERIFIED


def test_rejected_is_never_writable():
    with pytest.raises(ValueError):
        status_pair(E.professor, "rejected")


def test_legacy_alias_and_consistency():
    assert normalise_data_status("needs_verification") == DataStatus.needs_review
    assert is_consistent(E.professor, "verified", "VERIFIED")
    assert is_consistent(E.university, "needs_verification", "PENDING_REVIEW")
    assert not is_consistent(E.professor, "verified", "UNVERIFIED")
    assert not is_consistent(E.professor, "bogus", "VERIFIED")
    assert is_consistent(E.program, None, "VERIFIED") and is_consistent(E.professor, None, None)


def test_timestamps_follow_the_columns_each_model_has():
    fetched = datetime(2026, 10, 19, tzinfo=timezone.utc)
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert status_timestamps(E.professor, ProposedStatus.verified, fetched, now) == {"lastCheckedAt": now, "verifiedAt": fetched, "lastVerifiedAt": fetched}
    assert status_timestamps(E.university, ProposedStatus.verified, fetched, now) == {"lastCheckedAt": now, "verifiedAt": fetched}
    assert status_timestamps(E.program, ProposedStatus.verified, fetched, now) == {"lastCheckedAt": now, "lastVerifiedAt": fetched}
    assert status_timestamps(E.professor, ProposedStatus.needs_review, fetched, now) == {"lastCheckedAt": now}
