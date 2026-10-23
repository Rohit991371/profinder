"""Needs pydantic (real validators run here). Wraps the pure logic into ProfessorRecord / Candidate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from collections import Counter

from profinder_pipeline.extract.css import ProfileData
from profinder_pipeline.schemas.enums import Decision, ProposedStatus
from profinder_pipeline.utils.placeholders import PlaceholderRegistry
from profinder_pipeline.validate.professor import to_candidate
from profinder_pipeline.validate.professor_logic import SourcePage, evaluate_person
from extract_helpers import ECNU, NOW, TSINGHUA, fake_romanise, make_context


def _cands(university, fixture, **kw):
    ctx, lp = make_context(university, fixture, **kw)
    reg = PlaceholderRegistry.from_yaml()
    return [to_candidate(evaluate_person(r, None, None, ctx, romanise_fn=fake_romanise), university, reg, NOW) for r in lp.people], ctx, lp


def test_tsinghua_candidates_are_valid_and_consistent():
    cands, _ctx, _lp = _cands(TSINGHUA, "tsinghua_list.html")
    assert Counter(c.decision.value for c in cands) == {"auto_accept": 115, "needs_review": 8, "rejected": 12}
    assert len({c.natural_key for c in cands}) == 135
    for c in cands:
        p = c.payload
        if c.decision == Decision.auto_accept:
            assert c.proposed_status == ProposedStatus.verified and p.dataStatus.value == "verified" and p.verificationStatus.value == "VERIFIED"
            assert p.verifiedAt is not None and p.profileIsPersonal and p.profileUrl and p.email and p.position
            assert not [k for k in c.checks if k.blocking and not k.passed]
        if c.decision == Decision.needs_review:
            assert p.dataStatus.value == "needs_review" and p.verifiedAt is None
        assert p.dataSource == "official_faculty_page" and p.confidence is not None
    assert all(c.reject_reason for c in cands if c.decision == Decision.rejected)


def test_placeholder_interests_block_verification():
    ctx, lp = make_context(TSINGHUA, "tsinghua_list.html")
    reg = PlaceholderRegistry.from_yaml()
    prof = ProfileData(interests_lines=["computer science"])
    pp = SourcePage("https://x/", 200, NOW, "研究领域\ncomputer science")
    b = evaluate_person(lp.people[0], prof, pp, ctx, romanise_fn=fake_romanise)
    c = to_candidate(b, TSINGHUA, reg, NOW)
    assert c.decision == Decision.needs_review and c.proposed_status == ProposedStatus.needs_review
    assert any(k.name == "B5_no_placeholders" and not k.passed for k in c.checks)


def test_llm_interests_are_marked_with_model_and_prompt_version():
    ctx, lp = make_context(TSINGHUA, "tsinghua_list.html")
    b = evaluate_person(lp.people[0], None, None, ctx, llm_interests="操作系统、分布式系统", romanise_fn=fake_romanise)
    c = to_candidate(b, TSINGHUA, PlaceholderRegistry.from_yaml(), NOW, model="m", prompt_version="interests-v1")
    assert c.model == "m" and c.prompt_version == "interests-v1" and c.extractor.value == "css"
    assert c.payload.researchAreas == ["Distributed Systems", "Operating Systems"]
    assert any(e.field == "researchInterests(llm)" for e in c.evidence)


def test_ecnu_english_name_and_no_profile_link():
    cands, _c, _l = _cands(ECNU, "ecnu_list.html")
    sha = next(c for c in cands if c.payload.nameZh == "Edwin Sha")
    assert sha.payload.firstName is None and sha.decision == Decision.needs_review
    assert any(c.payload.profileUrl is None and not c.payload.profileIsPersonal for c in cands)     # javascript:void(0) links
