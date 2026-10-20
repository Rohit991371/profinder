import collections.abc
import typing

import pytest

pytest.importorskip("pydantic")

from pydantic import BaseModel  # noqa: E402

from profinder_pipeline.schemas.candidate import Candidate, CheckResult  # noqa: E402
from profinder_pipeline.schemas.entities import (ProfessorRecord, ProgramRecord, RECORD_MODELS, ScholarshipRecord,  # noqa: E402
                                                 UniversityRecord, check_iso_date)
from profinder_pipeline.schemas.enums import Decision, EntityType, Extractor, ProposedStatus, RESEARCH_AREAS  # noqa: E402
from profinder_pipeline.utils.keys import md5_hex  # noqa: E402

BAD_ORIGINS = (dict, collections.abc.Mapping, typing.Any)


def _walk(annotation, seen):
    origin = typing.get_origin(annotation)
    if annotation in (dict, typing.Any, object) or origin in BAD_ORIGINS:
        seen.append(annotation)
    if origin is typing.Literal:
        return
    for arg in typing.get_args(annotation):
        _walk(arg, seen)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        for f in annotation.model_fields.values():
            _walk(f.annotation, seen)


def test_no_dict_or_any_fields_anywhere():
    from profinder_pipeline.extract.groq_probe import GroqInterests
    bad = []
    for model in [*RECORD_MODELS.values(), Candidate, GroqInterests]:
        for name, f in model.model_fields.items():
            seen = []
            _walk(f.annotation, seen)
            if seen:
                bad.append(f"{model.__name__}.{name}")
    assert not bad, f"dict/Any fields are forbidden (D15): {bad}"


def test_research_area_enum_matches_yaml():
    import yaml
    from profinder_pipeline.paths import CONFIG_DIR
    areas = yaml.safe_load((CONFIG_DIR / "research_areas.yml").read_text(encoding="utf-8"))["areas"]
    assert tuple(areas) == RESEARCH_AREAS and len(areas) == 24


def prof(**over):
    base = dict(name="Feng Jianhua", firstName="Jianhua", lastName="Feng", nameZh="冯建华", position="Professor",
                email="FengJH@tsinghua.edu.cn", profileUrl="https://www.cs.tsinghua.edu.cn/info/1111/3490.htm", profileIsPersonal=True)
    base.update(over)
    return ProfessorRecord(**base)


def test_professor_ok_and_email_lowercased():
    p = prof()
    assert p.email == "fengjh@tsinghua.edu.cn" and p.researchAreas == []


@pytest.mark.parametrize("over", [
    {"email": "not-an-email"}, {"profileUrl": "javascript:void(0)"}, {"position": "教授"}, {"surprise": 1},
    {"profileIsPersonal": True, "profileUrl": None}, {"dataStatus": "verified", "verificationStatus": "UNVERIFIED"},
    {"researchAreas": ["Quantum Cooking"]}, {"confidence": 1.5}, {"researchKeywords": ",".join(f"k{i}" for i in range(11))},
    {"name": ""},
])
def test_professor_rejects_bad_values(over):
    with pytest.raises(Exception):
        prof(**over)


def test_professor_accepts_known_research_areas_and_blank_becomes_none():
    p = prof(researchAreas=["Databases", "Privacy"], lab="  ")
    assert p.lab is None and p.researchAreas == ["Databases", "Privacy"]


def test_exact_dates_only():
    assert check_iso_date("2027-03-31") == "2027-03-31"
    for bad in ("March 31 (typical)", "2027-3-1", "2027-02-30", "20270331"):
        with pytest.raises(ValueError):
            check_iso_date(bad)
    with pytest.raises(Exception):
        ProgramRecord(degree="PhD", major="CS", deadline="usually March")
    assert ProgramRecord(degree="PhD", major="CS", deadline="2027-03-31").deadline == "2027-03-31"
    with pytest.raises(Exception):
        UniversityRecord(name="X", applicationDeadline="soon")


def test_program_has_no_data_status_field_and_degree_is_enum():
    assert "dataStatus" not in ProgramRecord.model_fields
    with pytest.raises(Exception):
        ProgramRecord(degree="Doctorate", major="CS")


def test_scholarship_and_university_status_consistency():
    with pytest.raises(Exception):
        ScholarshipRecord(name="CSC", dataStatus="verified", verificationStatus="PENDING_REVIEW")
    assert UniversityRecord(name="Tsinghua University", dataStatus="needs_review", verificationStatus="PENDING_REVIEW").country == "China"


def cand(**over):
    base = dict(entity_type=EntityType.professor, natural_key=md5_hex("k"), payload=prof(), source_url="https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm",
                extractor=Extractor.css, proposed_status=ProposedStatus.needs_review, decision=Decision.needs_review)
    base.update(over)
    return Candidate(**base)


def test_candidate_round_trips_through_json_with_right_payload_type():
    c = cand()
    again = Candidate.model_validate_json(c.model_dump_json())
    assert isinstance(again.payload, ProfessorRecord) and again == c


def test_candidate_payload_must_match_entity_type():
    with pytest.raises(Exception):
        cand(entity_type=EntityType.scholarship)             # professor payload for a scholarship
    with pytest.raises(Exception):
        cand(entity_type=EntityType.publication)             # no record model yet
    s = cand(entity_type=EntityType.scholarship, payload={"name": "CSC"})
    assert isinstance(s.payload, ScholarshipRecord)


def test_candidate_rules():
    with pytest.raises(Exception):
        cand(decision=Decision.rejected)                      # needs a reject_reason
    assert cand(decision=Decision.rejected, reject_reason="non_faculty_role:工程师").reject_reason
    with pytest.raises(Exception):
        cand(decision=Decision.auto_accept, proposed_status=ProposedStatus.verified,
             checks=[CheckResult(name="B2_name_in_page_text", passed=False)])
    ok = cand(decision=Decision.auto_accept, proposed_status=ProposedStatus.verified,
              checks=[CheckResult(name="F_email_domain", passed=False, blocking=False)])
    assert ok.decision == Decision.auto_accept
    with pytest.raises(Exception):
        cand(natural_key="XYZ")
    with pytest.raises(Exception):
        cand(source_url="ftp://x")
