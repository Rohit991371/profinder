from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pypinyin")

from profinder_pipeline.extract.tsinghua_list import parse_list_html  # noqa: E402
from profinder_pipeline.schemas.enums import Decision, ProposedStatus  # noqa: E402
from profinder_pipeline.trial.tsinghua import (ALLOWED_EMAIL_DOMAINS, LIST_URL, _norm_ws, build_candidate, evidence_value)  # noqa: E402
from profinder_pipeline.extract.tsinghua_list import visible_text  # noqa: E402
from profinder_pipeline.utils.placeholders import PlaceholderRegistry  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def build(extra=(), status=None, source="saved_file", require_live=False):
    html = (FIX / "list_sample.html").read_text(encoding="utf-8")
    entries = parse_list_html(html, LIST_URL).entries
    text = _norm_ws(visible_text(html))
    reg = PlaceholderRegistry.from_yaml()
    return {e.name_raw: build_candidate(e, "2026-10-19T00:00:00+00:00", status, source, text, ALLOWED_EMAIL_DOMAINS + list(extra), reg, require_live, NOW)
            for e in entries}


def test_decisions_names_and_status_pairs():
    c = build()
    feng = c["冯建华"]
    assert feng.decision == Decision.auto_accept and feng.proposed_status == ProposedStatus.verified
    p = feng.payload
    assert (p.name, p.firstName, p.lastName, p.nameZh, p.position) == ("Feng Jianhua", "Jianhua", "Feng", "冯建华", "Professor")
    assert p.dataStatus.value == "verified" and p.verificationStatus.value == "VERIFIED" and p.verifiedAt and p.lastCheckedAt == NOW
    assert p.profileIsPersonal and p.profileUrl.endswith("/info/1111/3490.htm") and p.email == "fengjh@tsinghua.edu.cn"
    eng = c["陈宁"]
    assert eng.decision == Decision.rejected and eng.reject_reason.startswith("non_faculty_role")
    assert c["段海新（校内兼职）"].decision == Decision.needs_review
    assert c["段海新（校内兼职）"].payload.verifiedAt is None


def test_email_domain_rule_and_extra_domain():
    assert build()["吴建平"].payload.email is None
    assert evidence_value(build()["吴建平"], "email_seen_on_page") == "jianping@cernet.edu.cn"
    assert build(extra=["cernet.edu.cn"])["吴建平"].payload.email == "jianping@cernet.edu.cn"


def test_saved_file_counts_for_b1_unless_live_required():
    assert build()["冯建华"].decision == Decision.auto_accept
    strict = build(require_live=True)["冯建华"]
    assert strict.decision == Decision.needs_review and any(k.name == "B1_official_page_200" and not k.passed for k in strict.checks)
    assert build(status=200, source="live")["冯建华"].decision == Decision.auto_accept


def test_no_verified_candidate_fails_a_blocking_check():
    for cand in build().values():
        if cand.proposed_status == ProposedStatus.verified:
            assert all(k.passed for k in cand.checks if k.blocking)
