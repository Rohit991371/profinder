import pytest

from profinder_pipeline.utils.placeholders import PlaceholderRegistry, PlaceholderRule


def reg():
    return PlaceholderRegistry.from_yaml()


def test_yaml_loads_all_documented_rules():
    r = reg()
    keys = {(x.entity, x.field) for x in r.rules}
    assert {("University", "officialUrl"), ("Professor", "researchInterests"), ("Professor", "position"),
            ("Program", "deadline"), ("Program", "teachingLang"), ("Scholarship", "deadline")} <= keys


def test_university_official_url():
    r = reg()
    assert r.is_placeholder("University", "officialUrl", "https://www.campuschina.org/")
    assert not r.is_placeholder("University", "officialUrl", "https://www.tsinghua.edu.cn/")


def test_professor_interests():
    r = reg()
    assert r.is_placeholder("Professor", "researchInterests", "computer science")
    assert r.is_placeholder("Professor", "researchInterests", " Computer Science ")
    assert r.is_placeholder("Professor", "researchInterests", "Listed on the official Fudan computer science faculty directory.")
    assert not r.is_placeholder("Professor", "researchInterests", "database systems, privacy")
    assert not r.is_placeholder("Professor", "researchInterests", "computer science education research")


def test_professor_position_cjk_only():
    r = reg()
    assert r.is_placeholder("Professor", "position", "教授")
    assert not r.is_placeholder("Professor", "position", "Professor")
    assert not r.is_placeholder("Professor", "position", "教授 Professor")


def test_program_and_scholarship_templates():
    r = reg()
    assert r.is_placeholder("Program", "deadline", "Not confirmed \u2014 check the university international admissions page")
    assert not r.is_placeholder("Program", "deadline", "2027-03-31")
    assert r.is_placeholder("Program", "teachingLang", "English or Chinese \u2014 confirm on the official page")
    assert r.is_placeholder("Program", "teachingLang", "English or Chinese - confirm")
    assert not r.is_placeholder("Program", "teachingLang", "English")
    assert r.is_placeholder("Scholarship", "deadline", "Not confirmed")
    assert r.is_placeholder("Scholarship", "deadline", "Check Campus China for dates")


def test_typical_deadline_rule_is_not_active_yet():
    assert not reg().is_placeholder("Program", "deadline", "March 31 (typical, confirm)")


def test_fillable_means_empty_or_placeholder_only():
    r = reg()
    assert r.is_fillable("University", "nameZh", None) and r.is_fillable("University", "nameZh", "  ")
    assert r.is_fillable("University", "officialUrl", "https://www.campuschina.org/")
    assert not r.is_fillable("University", "officialUrl", "https://www.pku.edu.cn/")
    assert not r.is_fillable("University", "nameZh", "清华大学")


def test_placeholder_fields_scan():
    r = reg()
    rec = {"position": "教授", "researchInterests": "computer science", "email": "a@b.edu.cn", "nameZh": None}
    assert r.placeholder_fields("Professor", rec) == ["position", "researchInterests"]


def test_bad_rules_are_rejected():
    with pytest.raises(ValueError):
        PlaceholderRegistry([PlaceholderRule("X", "y", "nope", "v")])
    with pytest.raises(ValueError):
        PlaceholderRegistry([PlaceholderRule("X", "y", "contains", None)])
    with pytest.raises(Exception):
        PlaceholderRegistry([PlaceholderRule("X", "y", "regex", "(unclosed")])
