"""Task 5.2: write modes (insert / enrich / propose / noop), placeholders, locking, status never lowered, idempotency."""
from datetime import datetime, timedelta, timezone

from profinder_pipeline.match.modes import (apply_plan, effective_decision, is_human_touched, plan_record, status_rank)
from profinder_pipeline.utils.placeholders import PlaceholderRegistry

REG = PlaceholderRegistry.from_yaml()
PAYLOAD = {"name": "Zhu Jun", "nameZh": "朱军", "position": "Professor", "email": "dcszj@tsinghua.edu.cn",
           "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm", "profileIsPersonal": True,
           "researchInterests": "机器学习", "school": "Department of Computer Science", "dataSource": "official_faculty_page",
           "dataStatus": "verified", "verificationStatus": "VERIFIED", "researchAreas": ["Machine Learning"]}


def row(**kw):
    base = {"id": "p1", "name": "Zhu Jun", "nameZh": None, "position": None, "email": None, "profileUrl": None, "profileIsPersonal": False,
            "researchInterests": None, "school": None, "dataSource": "openalex", "dataStatus": "unverified", "verificationStatus": "UNVERIFIED"}
    base.update(kw)
    return base


def plan(existing, **kw):
    kw.setdefault("status_writable", True)
    kw.setdefault("proposed_status", "verified")
    return plan_record("Professor", PAYLOAD, existing, registry=REG, existing_areas=kw.pop("existing_areas", set()), **kw)


def test_insert_when_no_existing_row():
    assert plan(None).action == "insert"


def test_enrich_fills_null_and_placeholders_only():
    p = plan(row(position="教授", researchInterests="computer science"))      # CJK-only position and keyword filler are placeholders
    fields = {c.field: c.kind for c in p.changes}
    assert p.action == "enrich"
    assert fields["position"] == fields["researchInterests"] == fields["email"] == "enrich"
    assert fields["profileIsPersonal"] == "enrich" and fields["researchAreas"] == "enrich"
    assert fields["dataStatus"] == fields["verificationStatus"] == "status"


def test_different_real_value_is_a_proposal_not_a_write():
    p = plan(row(email="old@tsinghua.edu.cn", position="Associate Professor"))
    kinds = {c.field: c.kind for c in p.changes}
    assert kinds["email"] == "propose" and kinds["position"] == "propose"
    new = apply_plan(row(email="old@tsinghua.edu.cn"), p, PAYLOAD)
    assert new["email"] == "old@tsinghua.edu.cn"          # untouched


def test_identical_values_are_noop_and_status_is_never_lowered():
    existing = row(nameZh="朱军", position="Professor", email="DCSZJ@tsinghua.edu.cn", school="Department of Computer Science",
                   profileUrl="https://www.cs.tsinghua.edu.cn/info/1111/3490.htm/", profileIsPersonal=True, researchInterests="机器学习",
                   dataStatus="verified", verificationStatus="VERIFIED")
    assert plan(existing, existing_areas={"Machine Learning"}).action == "noop"
    lower = plan(row(dataStatus="verified", verificationStatus="VERIFIED"), proposed_status="needs_review")
    assert not [c for c in lower.changes if c.field in ("dataStatus", "verificationStatus")]


def test_status_raised_only_for_writable_candidates_and_legacy_alias_counts_as_review():
    assert status_rank("needs_verification", "PENDING_REVIEW") == 1 and status_rank("unverified", "UNVERIFIED") == 0
    assert not [c for c in plan(row(), status_writable=False).changes if c.field == "dataStatus"]
    up = plan(row(dataStatus="needs_verification", verificationStatus="PENDING_REVIEW"))
    assert [c.after for c in up.changes if c.field == "dataStatus"] == ["verified"]


def test_locked_row_gets_proposals_only():
    p = plan(row(), locked=True)
    assert p.action == "propose" and all(c.kind == "propose" for c in p.changes) and p.notes
    assert apply_plan(row(), p, PAYLOAD) == row()


def test_insert_only_mode_leaves_existing_rows_alone():
    p = plan(row(), mode="insert-only")
    assert p.action == "noop" and not p.changes
    assert plan(None, mode="insert-only").action == "insert"


def test_name_and_datasource_of_existing_rows_never_change():
    p = plan(row(name="Jun Zhu", dataSource="manual"))
    assert not [c for c in p.changes if c.field in ("name", "dataSource", "country", "universityId")]


def test_profileIsPersonal_only_set_when_url_agrees():
    p = plan(row(profileUrl="https://dir.x.edu.cn/list.htm"))                      # different non-placeholder URL -> proposal
    assert {c.field: c.kind for c in p.changes}["profileUrl"] == "propose"
    assert "profileIsPersonal" not in {c.field for c in p.changes}


def test_idempotent_second_plan_is_noop():
    existing = row(position="教授", researchInterests="computer science")
    p1 = plan(existing)
    after = apply_plan(existing, p1, PAYLOAD)
    areas = set(after.get("researchAreas", []))
    p2 = plan(after, existing_areas=areas)
    assert p2.action == "noop", [(c.field, c.kind) for c in p2.changes]


def test_university_placeholder_url_is_replaced_but_real_url_is_proposed():
    pay = {"name": "Peking University", "officialUrl": "https://www.pku.edu.cn/", "province": "Beijing", "dataStatus": "verified", "verificationStatus": "VERIFIED"}
    ex = {"id": "u", "name": "Peking University", "officialUrl": "https://www.campuschina.org/", "province": "Beijing", "dataStatus": "needs_verification",
          "verificationStatus": "PENDING_REVIEW"}
    p = plan_record("University", pay, ex, registry=REG, proposed_status="verified", status_writable=True)
    kinds = {c.field: c.kind for c in p.changes}
    assert kinds == {"officialUrl": "enrich", "dataStatus": "status", "verificationStatus": "status"}
    ex2 = dict(ex, officialUrl="https://old.pku.edu.cn/")
    assert {c.field: c.kind for c in plan_record("University", pay, ex2, registry=REG).changes}["officialUrl"] == "propose"


def test_program_has_no_dataStatus_and_identity_fields_are_not_diffed():
    pay = {"degree": "Master", "major": "Computer Science", "officialName": "MSc CS", "verificationStatus": "VERIFIED"}
    ex = {"id": "g", "degree": "Master", "major": "Computer Science ", "officialName": None, "verificationStatus": "UNVERIFIED"}
    p = plan_record("Program", pay, ex, registry=REG, proposed_status="verified", status_writable=True)
    assert {c.field for c in p.changes} == {"officialName", "verificationStatus"}


def test_human_touched_rule():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not is_human_touched(t0, t0 + timedelta(seconds=30), None)
    assert is_human_touched(t0, t0 + timedelta(minutes=5), None)
    assert not is_human_touched(t0, t0 + timedelta(days=9), t0 + timedelta(days=9))      # synced by the pipeline itself
    assert is_human_touched(t0, t0 + timedelta(days=9, minutes=2), t0 + timedelta(days=9))
    assert is_human_touched(datetime(2026, 1, 1), datetime(2026, 1, 2), None)             # naive datetimes are read as UTC


def test_effective_decision_downgrades_on_matching_problem():
    bad = [{"name": "M_unique_match", "passed": False, "blocking": True}]
    assert effective_decision("auto_accept", bad) == "needs_review"
    assert effective_decision("auto_accept", [{"name": "M_unique_match", "passed": True}]) == "auto_accept"
    assert effective_decision("rejected", bad) == "rejected"


def test_chinese_department_does_not_override_existing_english_one():
    pay = dict(PAYLOAD, department="计算机软件研究所", school="计算机科学与技术系")
    p = plan(row(department="Department of Computer Science and Technology", school=None, researchInterests="x"))
    assert "department" not in {c.field for c in plan_record("Professor", pay, row(department="Department of Computer Science and Technology"),
                                                              registry=REG, existing_areas=set()).changes}
    assert {c.field: c.kind for c in plan_record("Professor", pay, row(department=None), registry=REG, existing_areas=set()).changes}["department"] == "enrich"
    assert p is not None


def test_repeated_computer_science_filler_is_a_placeholder():
    assert REG.is_placeholder("Professor", "researchInterests", "computer science; computer science; computer science")
    assert REG.is_placeholder("Professor", "researchInterests", "Computer Science")
    assert not REG.is_placeholder("Professor", "researchInterests", "computer science and databases")


def test_url_comparison_ignores_scheme_and_www():
    pay = {"name": "Beihang University", "officialUrl": "https://buaa.edu.cn/"}
    ex = {"id": "u", "name": "Beihang University", "officialUrl": "http://www.buaa.edu.cn"}
    assert plan_record("University", pay, ex, registry=REG).action == "noop"
