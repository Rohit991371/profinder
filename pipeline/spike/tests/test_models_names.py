import pytest

pytest.importorskip("pydantic")


def _valid(**over):
    from spike.models import ProfessorCandidateV0
    base = dict(name_zh="冯建华", name_note_zh=None, name="Feng Jianhua", first_name="Jianhua", last_name="Feng",
                position_zh="教授", position="Professor", role_class="faculty", school="CS", institute_zh=None,
                rank_group_zh=None, email="fengjh@tsinghua.edu.cn", email_seen_on_page="fengjh@tsinghua.edu.cn",
                email_domain_ok=True, profile_url="https://www.cs.tsinghua.edu.cn/info/1111/3490.htm",
                profile_is_personal=True, source_url="https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm",
                fetched_at="2026-10-19T00:00:00+00:00", extractor="css", decision="auto_accept", reasons=[])
    base.update(over)
    return ProfessorCandidateV0(**base)


def test_valid_candidate_ok():
    assert _valid().name == "Feng Jianhua"


def test_invalid_email_rejected():
    with pytest.raises(Exception):
        _valid(email="not-an-email")


def test_extra_field_forbidden():
    with pytest.raises(Exception):
        _valid(surprise="x")


def test_non_http_url_rejected():
    with pytest.raises(Exception):
        _valid(profile_url="javascript:void(0)")


def test_groq_schema_has_no_dict_fields():
    from spike.models import GroqInterests
    props = GroqInterests.model_json_schema()["properties"]
    assert set(props) == {"research_interests", "evidence_quote"}
    assert all("object" not in str(p.get("type")) for p in props.values())
    assert set(GroqInterests.model_json_schema()["required"]) == {"research_interests", "evidence_quote"}


def test_romanise_family_given():
    pytest.importorskip("pypinyin")
    from spike.names import romanise
    assert romanise("冯建华") == ("Feng Jianhua", "Jianhua", "Feng")
    assert romanise("唐杰") == ("Tang Jie", "Jie", "Tang")
    assert romanise("单霁翔")[2] == "Shan"
    assert romanise("欧阳锋")[2].lower().startswith("ouyang")
    assert romanise("John Smith") is None
