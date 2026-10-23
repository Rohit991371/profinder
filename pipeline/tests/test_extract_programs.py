"""Programs and scholarships (task 4.6). Needs pydantic. The model output is simulated; grounding and the policy rules are what is tested."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from profinder_pipeline.extract import llm as llm_mod
from profinder_pipeline.extract.llm import LlmProgram, LlmScholarship, ProgramsOut, programs_from_markdown
from profinder_pipeline.extract.llm_core import GroqClient, TokenLimiter
from profinder_pipeline.extract.programs import build_candidates, map_coverage, map_degree, map_language, map_scholarship_type
from profinder_pipeline.schemas.enums import Decision, ProposedStatus
from profinder_pipeline.utils.placeholders import PlaceholderRegistry
from profinder_pipeline.validate.professor_logic import SourcePage

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
PAGE_TEXT = """International Students Admissions 2027
Master's programmes taught in English: Computer Science and Technology; Software Engineering.
PhD programme: Computer Science and Technology (taught in English and Chinese).
Application deadline: March 31, 2027. Application fee: RMB 800. Tuition: RMB 30,000 per year.
Chinese Government Scholarship (CSC) covers full tuition and a monthly stipend. Scholarship application deadline: January 15, 2027.
Presidential Scholarship for outstanding PhD students."""


def page(status=200, host_ok=True):
    return SourcePage("https://admission.example.edu.cn/intl.htm", status, NOW, PAGE_TEXT, None, host_ok)


def prog(**kw):
    base = dict(degree="Master", major="Software Engineering", official_name=None, teaching_language="taught in English", deadline_text="Application deadline: March 31, 2027",
                tuition_text="Tuition: RMB 30,000 per year", application_fee_text="Application fee: RMB 800",
                evidence_quote="Master's programmes taught in English: Computer Science and Technology; Software Engineering.")
    base.update(kw)
    return LlmProgram(**base)


def build(out, pg=None, kind="admissions"):
    return build_candidates(out, pg or page(), "Example University", kind, PlaceholderRegistry.from_yaml(), NOW, "m")


def test_program_is_existence_only_and_dates_money_are_held_back():
    cands, drop = build(ProgramsOut(programs=[prog()], scholarships=[]))
    assert len(cands) == 1 and not any(drop.values())
    c = cands[0]
    p = c.payload
    assert (p.degree, p.major, p.teachingLang) == ("Master", "Software Engineering", "English")
    assert p.deadline is None and p.tuition is None and p.applicationFee is None and p.openingDate is None       # D7: never auto-stored
    held = {e.field: e.quote for e in c.evidence}
    assert held["requires_human:deadline"].startswith("Application deadline") and "requires_human:tuition" in held and "requires_human:applicationFee" in held
    assert any(k.name == "H_human_approval_pending" and not k.passed for k in c.checks)
    assert c.decision == Decision.auto_accept and c.proposed_status == ProposedStatus.verified and p.verificationStatus.value == "VERIFIED"
    assert p.programUrl == p.sourceUrl == "https://admission.example.edu.cn/intl.htm"


def test_ungrounded_or_degreeless_programs_are_dropped():
    out = ProgramsOut(programs=[prog(major="Quantum Basket Weaving", evidence_quote="Quantum Basket Weaving"),
                                prog(degree="Bachelor"), prog(degree=None), prog(major="  "),
                                prog(evidence_quote="a quote that is not on the page"), prog(), prog()], scholarships=[])
    cands, drop = build(out)
    assert len(cands) == 1 and drop == {"no_degree": 2, "no_major": 1, "ungrounded": 2, "duplicate": 1}


def test_held_back_text_that_is_not_on_the_page_is_not_kept():
    cands, _ = build(ProgramsOut(programs=[prog(deadline_text="Deadline: 1 January 2030", tuition_text=None, application_fee_text=None)], scholarships=[]))
    assert [e.field for e in cands[0].evidence] == ["program_quote"] and not any(k.name == "H_human_approval_pending" for k in cands[0].checks)


def test_page_not_200_or_off_domain_is_rejected():
    for pg in (page(status=404), page(host_ok=False)):
        cands, _ = build(ProgramsOut(programs=[prog()], scholarships=[]), pg)
        assert cands[0].decision == Decision.rejected and cands[0].reject_reason.startswith("B1")


def test_scholarships_are_always_needs_review():
    sc = LlmScholarship(name="Chinese Government Scholarship (CSC)", type_hint="Chinese Government Scholarship (CSC)", coverage_text="full tuition and a monthly stipend",
                        deadline_text="Scholarship application deadline: January 15, 2027",
                        evidence_quote="Chinese Government Scholarship (CSC) covers full tuition and a monthly stipend.")
    cands, _ = build(ProgramsOut(programs=[], scholarships=[sc, LlmScholarship(name="Invented Prize", type_hint=None, coverage_text=None, deadline_text=None, evidence_quote="Invented Prize")]),
                     kind="scholarship")
    assert len(cands) == 1
    c = cands[0]
    assert c.decision == Decision.needs_review and c.proposed_status == ProposedStatus.needs_review
    assert c.payload.dataStatus.value == "needs_review" and c.payload.verificationStatus.value == "PENDING_REVIEW"
    assert (c.payload.type, c.payload.coverage, c.payload.deadline) == ("CSC", "Full", None)
    assert any(e.field == "requires_human:deadline" for e in c.evidence)


def test_mappings_need_words_and_never_guess():
    assert map_degree("硕士") == "Master" and map_degree("Ph.D.") == "PhD" and map_degree("博士研究生") == "PhD" and map_degree("Bachelor") is None
    assert map_language("English-taught") == "English" and map_language("中文授课") == "Chinese" and map_language("English and Chinese") == "English and Chinese"
    assert map_language(None) is None and map_language("Mandarin") == "Chinese"
    assert map_scholarship_type("CSC") == "CSC" and map_scholarship_type("校长奖学金") == "Presidential" and map_scholarship_type("misc") is None
    assert map_coverage("全额奖学金") == "Full" and map_coverage("partial tuition") == "Partial" and map_coverage("stipend") is None


def test_programs_from_markdown_merges_chunks_and_counts_failures(tmp_path):
    llm_mod.LLM_CACHE_DIR = tmp_path / "cache"
    good = json.dumps({"programs": [prog().model_dump()], "scholarships": []})
    replies = [good, "broken", "still broken", good]
    t = [0.0]
    def transport(payload, headers, timeout):
        return 200, {"choices": [{"message": {"content": replies.pop(0)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, {}
    cl = GroqClient(api_key="k", model="m", transport=transport, limiter=TokenLimiter(clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)), sleep=lambda s: None)
    text = "\n\n".join(f"- Programme {i} " + "y" * 500 for i in range(18))
    out, failed, used = programs_from_markdown(cl, text, "ref", chunk_chars=3000, max_chunks=3)
    assert used == 3 and failed == 1 and len(out.programs) == 2
