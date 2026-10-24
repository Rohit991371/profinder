"""Task 5.1: matchers (pure Python, existing rows as dicts)."""
from profinder_pipeline.match.matchers import (ProfessorMatcher, ProgramMatcher, ScholarshipMatcher, UniversityMatcher,
                                               openalex_key, roman_key)

PROFS = [
    {"id": "p1", "name": "Zhu Jun", "nameZh": "朱军", "email": "dcszj@tsinghua.edu.cn", "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm", "department": None},
    {"id": "p2", "name": "Wang Wei", "nameZh": None, "email": None, "profileUrl": "https://openalex.org/A1", "department": "Computer Science"},
    {"id": "p3", "name": "Wei Wang", "nameZh": None, "email": None, "profileUrl": "https://openalex.org/A2", "department": "Computer Science"},
    {"id": "p4", "name": "Li Si", "nameZh": "李四", "email": "lisi@x.edu.cn", "profileUrl": "https://dir.x.edu.cn/list.htm", "department": None},
    {"id": "p5", "name": "Li Si", "nameZh": "李四", "email": "other@x.edu.cn", "profileUrl": "https://dir.x.edu.cn/list.htm", "department": None},
]


def test_professor_profile_url_wins_and_is_canonicalised():
    m = ProfessorMatcher(PROFS)
    r = m.match({"name": "Different Name", "profileUrl": "HTTPS://www.cs.tsinghua.edu.cn/info/1111/3490.htm#top", "profileIsPersonal": True})
    assert (r.status, r.entity_id, r.via) == ("unique", "p1", "profileUrl")


def test_chinese_only_name_matches_on_nameZh():
    r = ProfessorMatcher(PROFS).match({"name": "Zhu Jun", "nameZh": "朱 军", "profileUrl": None, "profileIsPersonal": False})
    assert (r.status, r.entity_id, r.via) == ("unique", "p1", "nameZh")


def test_email_match_case_insensitive():
    r = ProfessorMatcher(PROFS).match({"name": "X Y", "email": "DCSZJ@Tsinghua.edu.cn"})
    assert (r.status, r.entity_id, r.via) == ("unique", "p1", "email")


def test_two_rows_with_same_nameZh_is_ambiguous_never_merged():
    r = ProfessorMatcher(PROFS).match({"name": "Li Si", "nameZh": "李四"})
    assert r.status == "ambiguous" and set(r.ids) == {"p4", "p5"}


def test_levels_pointing_at_different_rows_are_ambiguous():
    r = ProfessorMatcher(PROFS).match({"name": "Zhu Jun", "nameZh": "朱军", "email": "lisi@x.edu.cn"})
    assert r.status == "ambiguous" and set(r.ids) == {"p1", "p4"}


def test_directory_url_is_never_used_for_matching_when_not_personal():
    r = ProfessorMatcher(PROFS).match({"name": "Nobody", "profileUrl": "https://dir.x.edu.cn/list.htm", "profileIsPersonal": False})
    assert r.status == "none"


def test_weak_level_needs_department_and_ignores_name_order():
    m = ProfessorMatcher([{"id": "a", "name": "Zou Qiang", "department": "Software Engineering"}])
    r = m.match({"name": "Qiang Zou", "department": "software  engineering"})
    assert (r.status, r.entity_id, r.via) == ("unique", "a", "name_department")
    assert m.match({"name": "Zou Qiang", "department": "Physics"}).status == "none"


def test_same_romanised_name_is_reported_but_not_merged():
    m = ProfessorMatcher(PROFS)
    r = m.match({"name": "Wei Wang", "department": "计算机系"})
    assert r.status == "none" and "same_romanised_name_rows=2" in (r.note or "")


def test_roman_key_and_openalex_key():
    assert roman_key("Zou  Qiang") == roman_key("qiang ZOU") and roman_key("朱军") is None
    assert openalex_key("https://openalex.org/I20231570") == openalex_key("i20231570") == "I20231570"


UNIS = [
    {"id": "u1", "name": "Tsinghua University", "nameZh": "清华大学", "openAlexId": "I99", "agencyNumber": "10003"},
    {"id": "u2", "name": "Beijing Normal University", "nameZh": "北京师范大学", "openAlexId": None, "agencyNumber": "10027"},
    {"id": "u3", "name": "China University of Petroleum (East China)", "nameZh": None, "openAlexId": None, "agencyNumber": None},
    {"id": "u4", "name": "China University of Petroleum Beijing", "nameZh": None, "openAlexId": None, "agencyNumber": None},
]


def test_university_levels_and_name_resolution():
    m = UniversityMatcher(UNIS)
    assert m.match({"name": "X", "openAlexId": "https://openalex.org/I99"}).entity_id == "u1"
    assert m.match({"name": "beijing normal university"}).entity_id == "u2"
    assert m.match({"name": "Other", "nameZh": "北京师范大学"}).via == "nameZh"
    assert m.match({"name": "Unknown University"}).status == "none"
    assert m.id_for_name("China University Of Petroleum (East China)") == "u3"
    assert m.id_for_name("Nowhere") is None


def test_university_openalex_and_name_conflict_is_ambiguous():
    r = UniversityMatcher(UNIS).match({"name": "Beijing Normal University", "openAlexId": "I99"})
    assert r.status == "ambiguous"


def test_program_and_scholarship():
    pm = ProgramMatcher([{"id": "g1", "degree": "Master", "major": "Computer Science", "programUrl": None},
                         {"id": "g2", "degree": "PhD", "major": "Computer Science", "programUrl": "https://a.edu.cn/p"}])
    assert pm.match({"degree": "master", "major": "Computer  Science"}).entity_id == "g1"
    assert pm.match({"degree": "PhD", "major": "Other", "programUrl": "https://a.edu.cn/p/"}).entity_id == "g2"
    sm = ScholarshipMatcher([{"id": "s1", "name": "Chinese Government Scholarship (CSC)", "officialUrl": None}])
    assert sm.match({"name": "chinese government scholarship (csc)"}).entity_id == "s1"
    assert sm.match({"name": "Zhishan Scholarship"}).status == "none"
