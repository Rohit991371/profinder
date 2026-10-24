"""Tasks 5.5/5.6: loader, dry-run parity, idempotency, non-destructive rules, rollback (sqlite3 stands in for PostgreSQL)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlite_db import SqliteDb

from profinder_pipeline.load.loader import LoadAbort, ReleaseData, apply_ops, plan_load, run_load, user_counts
from profinder_pipeline.load.rollback import plan_rollback, run_rollback
from profinder_pipeline.utils.placeholders import PlaceholderRegistry

REG = PlaceholderRegistry.from_yaml()
RID = "11111111-2222-3333-4444-555555555555"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
STAMPS = {"verifiedAt": "2026-10-24T06:00:00Z", "lastVerifiedAt": "2026-10-24T06:00:00Z", "lastCheckedAt": "2026-10-24T06:00:00Z", "confidence": 0.95}
OK = {"dataStatus": "verified", "verificationStatus": "VERIFIED"}


def make_db():
    db = SqliteDb()
    iso = T0.isoformat()
    db.insert("University", id="u1", name="Tsinghua University", nameZh=None, officialUrl="https://www.campuschina.org/", agencyNumber="10003",
              dataStatus="needs_verification", verificationStatus="PENDING_REVIEW", createdAt=iso, updatedAt=iso)
    base = dict(universityId="u1", createdAt=iso, updatedAt=iso, lastSyncedAt=iso, dataStatus="unverified", verificationStatus="UNVERIFIED", dataSource="official_import")
    db.insert("Professor", id="p1", name="Zhu Jun", nameZh="朱军", position="教授", researchInterests="computer science", profileIsPersonal=0, **base)
    db.insert("Professor", id="p2", name="Li Si", nameZh="李四", email="old@tsinghua.edu.cn", profileIsPersonal=0,
              **{**base, "updatedAt": (T0 + timedelta(days=5)).isoformat()})      # edited by a person after the last sync
    for i, n in enumerate(("Computer Science", "Machine Learning", "Databases")):
        db.insert("ResearchArea", id=f"a{i}", name=n)
    db.insert("ProfessorResearchArea", professorId="p1", researchAreaId="a0")
    for t in ("Student", "Application"):
        db.insert(t, id=f"{t}1")
    return db


def release() -> ReleaseData:
    src = lambda cid, et, eid, url: [  # noqa: E731
        {"table": "SourceRecord", "candidate_id": cid, "entityType": et, "entityId": eid, "url": url, "sourceType": "OFFICIAL_UNIVERSITY",
         "verificationStatus": "VERIFIED", "checkedAt": "2026-10-24T06:00:00+00:00", "verifiedAt": "2026-10-24T06:00:00+00:00", "confidence": None, "httpStatus": 200, "contentHash": "h"},
        {"table": "ProfessorSource", "candidate_id": cid, "entityType": et, "entityId": eid, "source": "official_faculty_page", "externalId": f"k-{cid}",
         "sourceUrl": url, "fetchedAt": "2026-10-24T06:00:00+00:00"}][: 2 if et == "PROFESSOR" else 1]
    lines = {
        "university": [{"action": "enrich", "candidate_id": "cu", "entity_id": "u1", "natural_key": "ku",
                        "fields": {"officialUrl": "https://www.tsinghua.edu.cn/", **OK, **STAMPS}, "research_areas": []}],
        "professor": [
            {"action": "enrich", "candidate_id": "c1", "entity_id": "p1", "natural_key": "k1",
             "fields": {"position": "Professor", "email": "zj@tsinghua.edu.cn", **OK, **STAMPS}, "research_areas": ["Computer Science", "Machine Learning"]},
            {"action": "insert", "candidate_id": "c3", "entity_id": None, "natural_key": "k3",
             "fields": {"name": "Wang Xin", "nameZh": "王新", "universityId": "u1", "position": "Professor", "email": "wx@tsinghua.edu.cn",
                        "profileUrl": "https://www.cs.tsinghua.edu.cn/info/1/9.htm", "profileIsPersonal": True, "dataSource": "official_faculty_page", **OK, **STAMPS},
             "research_areas": ["Databases"]}],
        "program": [], "scholarship": []}
    sources = {"cu": src("cu", "UNIVERSITY", "u1", "https://www.tsinghua.edu.cn/")[:1], "c1": src("c1", "PROFESSOR", "p1", "https://www.cs.tsinghua.edu.cn/info/1/1.htm"),
               "c3": src("c3", "PROFESSOR", None, "https://www.cs.tsinghua.edu.cn/info/1/9.htm")}
    proposals = [{"candidate_id": "c2", "entityType": "PROFESSOR", "entityId": "p2", "changeType": "PROPOSED_UPDATE",
                  "beforeJson": {"email": "old@tsinghua.edu.cn"}, "afterJson": {"email": "new@tsinghua.edu.cn"}, "sourceUrl": "https://www.cs.tsinghua.edu.cn/info/1/2.htm"}]
    return ReleaseData(lines, sources, proposals)


def tables(db):
    return {t: db.count(t) for t in ("University", "Professor", "ResearchArea", "ProfessorResearchArea", "ProfessorSource", "SourceRecord", "ChangeLog", "Student", "Application")}


def test_dry_run_writes_nothing_and_reports_the_plan():
    db = make_db()
    before = tables(db)
    out = run_load(db, release(), RID, REG, apply=False, now=NOW)
    assert tables(db) == before and not out["applied"]
    s = out["plan"]["summary"]
    assert s["enrich:Professor"] == 1 and s["insert:Professor"] == 1 and s["enrich:University"] == 1 and s["insert:ProfessorSource"] == 2
    assert s["insert:SourceRecord"] == 3 and s["link:ProfessorResearchArea"] == 2 and s["insert:ChangeLog(proposal)"] == 1 and s["insert:ChangeLog"] == 3


def test_apply_is_non_destructive_idempotent_and_leaves_user_tables_alone():
    db = make_db()
    before = tables(db)
    out = run_load(db, release(), RID, REG, apply=True, now=NOW)          # run_load re-plans inside and raises if the second plan is not empty
    after = tables(db)
    assert out["applied"] and all(after[t] >= before[t] for t in before)
    assert after["Student"] == before["Student"] == 1 and after["Application"] == 1
    p1 = db.rows('SELECT * FROM "Professor" WHERE id = %s', ("p1",))[0]
    assert p1["position"] == "Professor" and p1["email"] == "zj@tsinghua.edu.cn" and p1["verificationStatus"] == "VERIFIED" and p1["name"] == "Zhu Jun"
    assert p1["researchInterests"] == "computer science"                       # not in the release: untouched
    assert db.rows('SELECT "officialUrl", name FROM "University"')[0] == {"officialUrl": "https://www.tsinghua.edu.cn/", "name": "Tsinghua University"}
    new = db.rows('SELECT * FROM "Professor" WHERE "nameZh" = %s', ("王新",))[0]
    assert new["universityId"] == "u1" and new["profileIsPersonal"] == 1 and new["lastSyncedAt"] is not None
    assert {r["name"] for r in db.rows('SELECT ra.name FROM "ProfessorResearchArea" l JOIN "ResearchArea" ra ON ra.id = l."researchAreaId" WHERE l."professorId" = %s', ("p1",))} == {"Computer Science", "Machine Learning"}
    assert db.count("SourceRecord") == 3 and db.count("ProfessorSource") == 2
    # a human-edited row only receives a proposal
    p2 = db.rows('SELECT email FROM "Professor" WHERE id = %s', ("p2",))[0]
    assert p2["email"] == "old@tsinghua.edu.cn"
    assert db.rows('SELECT "changeType" FROM "ChangeLog" WHERE "entityId" = %s', ("p2",))[0]["changeType"] == "PROPOSED_UPDATE"
    again = run_load(db, release(), RID, REG, apply=False, now=NOW)
    assert again["plan"]["operations"] == 0 and again["plan"]["summary"] == {"noop:Professor": 2, "noop:University": 1}


def test_dry_run_parity_with_the_applied_operations():
    db = make_db()
    plan = plan_load(db, release(), RID, REG, NOW)
    done = apply_ops(db, plan.ops)
    assert sum(done.values()) == len(plan.ops)
    assert done["insert:Professor"] == 1 and done["update:Professor"] == 1 and done["link:ProfessorResearchArea"] == 2


def test_second_load_of_the_same_release_changes_nothing():
    db = make_db()
    run_load(db, release(), RID, REG, apply=True, now=NOW)
    snap = {t: db.dump(t) for t in ("Professor", "University", "SourceRecord", "ProfessorSource", "ChangeLog", "ProfessorResearchArea")}
    out = run_load(db, release(), RID, REG, apply=True, now=NOW + timedelta(hours=1))
    assert out["plan"]["operations"] == 0
    assert snap == {t: db.dump(t) for t in snap}


def test_changed_row_aborts_instead_of_overwriting():
    db = make_db()
    plan = plan_load(db, release(), RID, REG, NOW)
    db.execute('UPDATE "Professor" SET "updatedAt" = %s WHERE id = %s', ("2026-10-24T11:59:00", "p1"))      # somebody saved p1 meanwhile
    with pytest.raises(LoadAbort):
        apply_ops(db, plan.ops)


def test_field_filled_in_the_meantime_is_not_overwritten():
    db = make_db()
    db.execute('UPDATE "Professor" SET email = %s WHERE id = %s', ("mine@tsinghua.edu.cn", "p1"))          # same timestamps: the loader sees a real value now
    out = run_load(db, release(), RID, REG, apply=True, now=NOW)
    assert db.rows('SELECT email FROM "Professor" WHERE id = %s', ("p1",))[0]["email"] == "mine@tsinghua.edu.cn"
    assert out["plan"]["summary"]["insert:ChangeLog(proposal)"] == 2


def test_rollback_restores_the_original_rows_and_removes_what_was_inserted():
    db = make_db()
    keep = ("updatedAt", "lastSyncedAt")
    snap = {t: db.dump(t, keep) for t in ("University", "Professor", "ProfessorResearchArea")}
    run_load(db, release(), RID, REG, apply=True, now=NOW)
    assert db.count("Professor") == 3
    dry = run_rollback(db, RID, apply=False, now=NOW + timedelta(hours=1))
    assert not dry["applied"] and db.count("Professor") == 3
    out = run_rollback(db, RID, apply=True, now=NOW + timedelta(hours=1))
    assert out["applied"]
    assert snap == {t: db.dump(t, keep) for t in snap}
    assert db.count("SourceRecord") == 0 and db.count("ProfessorSource") == 0 and db.count("Student") == 1
    kinds = {r["changeType"] for r in db.rows('SELECT "changeType" FROM "ChangeLog"')}
    assert "PROPOSED_UPDATE" not in kinds and "PIPELINE_ROLLED_BACK" in kinds and "PIPELINE_CREATED" in kinds
    assert not plan_rollback(db, RID, NOW + timedelta(hours=2)).ops                     # a second rollback has nothing left to do


def test_rollback_keeps_what_a_person_changed_since():
    db = make_db()
    run_load(db, release(), RID, REG, apply=True, now=NOW)
    db.execute('UPDATE "Professor" SET position = %s WHERE id = %s', ("Chair Professor", "p1"))            # edited after the load
    new_id = db.rows('SELECT id FROM "Professor" WHERE "nameZh" = %s', ("王新",))[0]["id"]
    db.execute('UPDATE "Professor" SET "updatedAt" = %s WHERE id = %s', ((NOW + timedelta(days=2)).isoformat(), new_id))
    plan = plan_rollback(db, RID, NOW + timedelta(days=3))
    run_rollback(db, RID, apply=True, now=NOW + timedelta(days=3))
    assert db.rows('SELECT position FROM "Professor" WHERE id = %s', ("p1",))[0]["position"] == "Chair Professor"
    assert db.rows('SELECT 1 AS x FROM "Professor" WHERE id = %s', (new_id,))
    assert any("edited after the load" in s for s in plan.skipped) and any("changed since the load" in s for s in plan.skipped)


def test_rollback_does_not_delete_a_professor_users_saved():
    db = make_db()
    run_load(db, release(), RID, REG, apply=True, now=NOW)
    new_id = db.rows('SELECT id FROM "Professor" WHERE "nameZh" = %s', ("王新",))[0]["id"]
    db.insert("SavedProfessor", id="s1", professorId=new_id)
    plan = plan_rollback(db, RID, NOW + timedelta(minutes=5))
    assert any("user data refers to it" in s for s in plan.skipped)
    run_rollback(db, RID, apply=True, now=NOW + timedelta(minutes=5))
    assert db.rows('SELECT 1 AS x FROM "Professor" WHERE id = %s', (new_id,)) and db.count("SavedProfessor") == 1


def test_user_counts_only_lists_existing_tables():
    assert user_counts(make_db()) == {"Student": 1, "Application": 1, "SavedProfessor": 0}
