"""Task 6.1: OpenAlex enrichment of EXISTING official-sourced professors only. The OpenAlex response below is SYNTHETIC, written from the
documented shape: `enrich probe` is the check against the live API. sqlite3 stands in for PostgreSQL (same SQL)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlite_db import SqliteDb

from profinder_pipeline.enrich.authors import (Author, bare_orcid, decide, name_matches, parse_author, parse_response, query_name,
                                               search_authors, short_id)
from profinder_pipeline.enrich.engine import (build_line, detect_url_style, eligible, format_openalex_id, format_orcid, plan_enrich_load,
                                              run_enrich_load)
from profinder_pipeline.load.loader import LoadAbort, user_counts
from profinder_pipeline.load.rollback import run_rollback
from profinder_pipeline.utils.placeholders import PlaceholderRegistry

REG = PlaceholderRegistry.from_yaml()
EID = "enr_2026-10-26_abc123"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
INST = "I20231570"


def raw_author(aid="A111", name="Jun Zhu", insts=(INST,), orcid="https://orcid.org/0000-0002-1234-5678", works=120, fields=("Computer Science",)):
    return {"id": f"https://openalex.org/{aid}", "display_name": name, "display_name_alternatives": ["J. Zhu"], "orcid": orcid, "works_count": works,
            "cited_by_count": 5000, "last_known_institutions": [{"id": f"https://openalex.org/{i}", "display_name": "X"} for i in insts],
            "topics": [{"display_name": "Machine learning", "field": {"display_name": f}} for f in fields]}


def prof(**kw):
    base = {"id": "p1", "name": "Zhu Jun", "nameZh": "朱军", "universityId": "u1", "dataSource": "official_faculty_page", "orcid": None, "openAlexId": None}
    return {**base, **kw}


# ------------------------------------------------------------------------------------------------ parsing and the strong-match rule
def test_parse_author_and_ids():
    a = parse_author(raw_author())
    assert a.openalex_id == "A111" and a.orcid == "0000-0002-1234-5678" and a.works_count == 120
    assert INST in a.institution_ids and a.fields == {"Computer Science"} and a.url == "https://openalex.org/A111"
    assert short_id("https://openalex.org/I20231570") == "I20231570" and bare_orcid("https://orcid.org/0000-0002-1234-567x") == "0000-0002-1234-567X"
    assert parse_author({"id": None, "display_name": "x"}) is None
    assert len(parse_response({"results": [raw_author(), {"nonsense": 1}, raw_author("A2", "Li Si")]})) == 2
    assert parse_response({}) == []


def test_affiliations_shape_is_read_too():
    o = raw_author()
    o.pop("last_known_institutions")
    o["affiliations"] = [{"institution": {"id": "https://openalex.org/I999"}, "years": [2020]}]
    assert "I999" in parse_author(o).institution_ids


def test_name_matching_is_order_insensitive_and_exact():
    a = parse_author(raw_author(name="Jun Zhu"))
    assert name_matches("Zhu Jun", a)
    assert not name_matches("Zhu Junjie", a) and not name_matches("Li Jun", a)
    assert query_name("Zhu Jun") == "Jun Zhu" and query_name("Ouyang Wanli") == "Wanli Ouyang" and query_name("Zhou") == "Zhou"


def test_strong_match_needs_name_institution_uniqueness_and_fields():
    a = parse_author(raw_author())
    o = decide(prof(), [a], INST)
    assert o.status == "strong" and o.author.openalex_id == "A111"
    assert decide(prof(), [], INST).status == "none"
    assert decide(prof(), [parse_author(raw_author(name="Li Si"))], INST).reason == "no_exact_name_match"
    other = parse_author(raw_author(insts=("I1",)))
    assert decide(prof(), [other], INST).reason == "name_matches_but_not_at_this_university"
    twin = parse_author(raw_author("A222"))
    o2 = decide(prof(), [a, twin], INST)
    assert o2.status == "review" and o2.reason == "several_authors_with_same_name_at_university" and len(o2.others) == 1
    assert decide(prof(), [parse_author(raw_author(fields=("Medicine",)))], INST).reason == "fields_do_not_match_allowed_fields"
    assert decide(prof(), [parse_author(raw_author(fields=()))], INST).reason == "no_topics_to_check_fields"
    assert decide(prof(), [parse_author(raw_author(works=0))], INST).reason == "author_without_works"


def test_skips_and_orcid_rules():
    a = parse_author(raw_author())
    assert decide(prof(openAlexId="A9"), [a], INST).status == "skip"
    assert decide(prof(), [a], None).reason == "university_has_no_openalex_id"
    assert decide(prof(name="朱军"), [a], INST).reason == "no_latin_name"
    assert decide(prof(orcid="0000-0001-1111-2222"), [a], INST).reason == "orcid_conflict"
    same = parse_author(raw_author(fields=("Medicine",)))                   # equal ORCID: concordance is not needed
    assert decide(prof(orcid="https://orcid.org/0000-0002-1234-5678"), [same], INST).reason == "orcid_equal"


def test_eligibility_never_enriches_openalex_or_unsourced_rows():
    assert eligible(prof()) is None
    assert eligible(prof(dataSource="openalex")) == "not_official_sourced"
    assert eligible(prof(dataSource=None)) == "not_official_sourced"
    assert eligible(prof(openAlexId="A1")) == "already_has_openalex_id"


def test_formats_follow_the_existing_rows():
    assert detect_url_style(["https://openalex.org/A1", "https://openalex.org/A2", "A3"]) == "url"
    assert detect_url_style(["A1", "A2", "https://x"]) == "bare" and detect_url_style([]) == "url"
    a = parse_author(raw_author())
    assert format_openalex_id(a, "url") == "https://openalex.org/A111" and format_openalex_id(a, "bare") == "A111"
    assert format_orcid("0000-0002-1234-5678", "bare") == "0000-0002-1234-5678"
    line = build_line(prof(), a, "r", {}, {"openAlexId": "bare", "orcid": "url"}, NOW)
    assert line["fields"] == {"openAlexId": "A111", "orcid": "https://orcid.org/0000-0002-1234-5678", "publicationCount": 120}
    own = build_line(prof(orcid="0000-0002-1234-5678"), a, "r", {}, {"openAlexId": "bare", "orcid": "url"}, NOW)
    assert "orcid" not in own["fields"]                                      # an existing ORCID is never rewritten


def test_search_is_cached_and_never_stores_the_key(tmp_path):
    calls = []

    def fake(client, url, params=None, **kw):
        calls.append(params)
        return {"results": [raw_author()]}

    a1, net1 = search_authors(None, "Jun Zhu", INST, tmp_path, "SECRET-KEY", get_json=fake, pause=0)
    a2, net2 = search_authors(None, "Jun Zhu", INST, tmp_path, "SECRET-KEY", get_json=fake, pause=0)
    assert net1 and not net2 and len(calls) == 1 and a1 == a2
    assert calls[0]["filter"] == f"last_known_institutions.id:{INST}" and calls[0]["api_key"] == "SECRET-KEY"
    assert "SECRET-KEY" not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))


# ------------------------------------------------------------------------------------------------ loader (sqlite stand-in)
def make_db():
    db = SqliteDb()
    for col, typ in (("orcid", "TEXT"), ("openAlexId", "TEXT"), ("publicationCount", "INTEGER")):
        db.execute(f'ALTER TABLE "Professor" ADD COLUMN "{col}" {typ}')
    iso = T0.isoformat()
    db.insert("University", id="u1", name="Tsinghua University", createdAt=iso, updatedAt=iso)
    base = dict(universityId="u1", createdAt=iso, updatedAt=iso, lastSyncedAt=iso, dataStatus="verified", verificationStatus="VERIFIED", profileIsPersonal=0)
    db.insert("Professor", id="p1", name="Zhu Jun", nameZh="朱军", dataSource="official_faculty_page", **base)
    db.insert("Professor", id="p2", name="Li Si", nameZh="李四", dataSource="official_faculty_page", orcid="0000-0001-0000-0001", **base)
    db.insert("Professor", id="p3", name="Old Row", dataSource="openalex", **base)
    db.insert("Professor", id="p4", name="Edited Person", dataSource="official_import", **{**base, "updatedAt": (T0 + timedelta(days=9)).isoformat()})
    db.insert("Student", id="s1")
    db.insert("Application", id="a1")
    return db


def lines():
    st = {"openAlexId": "url", "orcid": "url"}
    a1 = parse_author(raw_author("A111", "Jun Zhu"))
    a2 = parse_author(raw_author("A222", "Si Li", orcid="https://orcid.org/0000-0009-9999-9999", works=7))
    a3 = parse_author(raw_author("A333", "Old Row"))
    a4 = parse_author(raw_author("A444", "Person Edited"))
    return [build_line(prof(id="p1"), a1, "r", {}, st, NOW), build_line(prof(id="p2", name="Li Si", nameZh="李四"), a2, "r", {}, st, NOW),
            build_line(prof(id="p3", name="Old Row", dataSource="openalex"), a3, "r", {}, st, NOW),
            build_line(prof(id="p4", name="Edited Person"), a4, "r", {}, st, NOW)]


def row(db, pid):
    return db.rows('SELECT * FROM "Professor" WHERE id = %s', (pid,))[0]


def test_dry_run_writes_nothing_and_matches_apply():
    db = make_db()
    before = {t: db.dump(t) for t in ("Professor", "ChangeLog", "ProfessorSource", "SourceRecord")}
    res = run_enrich_load(db, lines(), EID, REG, apply=False, now=NOW)
    assert not res["applied"] and {t: db.dump(t) for t in before} == before
    plan_summary = res["plan"]["summary"]
    res2 = run_enrich_load(db, lines(), EID, REG, apply=True, now=NOW)
    assert res2["applied"] and res2["plan"]["summary"] == plan_summary
    assert plan_summary["enrich:Professor"] == 2 and plan_summary["insert:ProfessorSource"] == 2          # p1 and p2 (orcid kept)


def test_apply_is_additive_and_non_destructive():
    db = make_db()
    users = user_counts(db)
    n_prof = db.count("Professor")
    run_enrich_load(db, lines(), EID, REG, apply=True, now=NOW)
    p1, p2 = row(db, "p1"), row(db, "p2")
    assert p1["openAlexId"] == "https://openalex.org/A111" and p1["orcid"] == "https://orcid.org/0000-0002-1234-5678" and p1["publicationCount"] == 120
    assert p1["dataStatus"] == "verified" and p1["name"] == "Zhu Jun" and p1["nameZh"] == "朱军"              # status and identity untouched
    assert p2["openAlexId"] == "https://openalex.org/A222" and p2["orcid"] == "0000-0001-0000-0001"          # existing ORCID NOT overwritten
    cl = db.rows('SELECT * FROM "ChangeLog" WHERE "entityId" = %s', ("p2",))
    prop = [c for c in cl if c["changeType"] == "PROPOSED_UPDATE"]
    assert len(prop) == 1 and json.loads(prop[0]["afterJson"])["orcid"] == "https://orcid.org/0000-0009-9999-9999"
    assert row(db, "p3")["openAlexId"] is None                                                               # openalex-sourced row skipped
    assert db.count("Professor") == n_prof and user_counts(db) == users
    src = db.rows('SELECT * FROM "SourceRecord" WHERE "entityId" = %s', ("p1",))
    assert src[0]["sourceType"] == "OPENALEX" and src[0]["verificationStatus"] == "UNVERIFIED"


def test_second_load_changes_nothing_and_human_touched_rows_only_get_proposals():
    db = make_db()
    run_enrich_load(db, lines(), EID, REG, apply=True, now=NOW)
    again = plan_enrich_load(db, lines(), EID, REG, NOW)
    assert not again.ops
    p4 = row(db, "p4")
    assert p4["openAlexId"] is None                                                                         # edited by a person: locked (strict)
    log = db.rows('SELECT * FROM "ChangeLog" WHERE "entityId" = %s', ("p4",))
    assert [c["changeType"] for c in log] == ["PROPOSED_UPDATE"]
    db2 = make_db()
    db2.execute('UPDATE "Professor" SET "lastSyncedAt" = NULL WHERE id = %s', ("p4",))
    relaxed = plan_enrich_load(db2, lines(), EID, REG, NOW, lock_policy="import-ok")
    assert any(o.where_id == "p4" for o in relaxed.ops if o.kind == "update")                               # official_import rows never synced by us: ok under import-ok


def test_a_row_saved_meanwhile_aborts_the_whole_load():
    db = make_db()
    plan = plan_enrich_load(db, lines(), EID, REG, NOW)
    db.execute('UPDATE "Professor" SET "updatedAt" = %s WHERE id = %s', ((T0 + timedelta(days=30)).isoformat(), "p1"))
    from profinder_pipeline.load.loader import apply_ops
    with pytest.raises(LoadAbort):
        apply_ops(db, plan.ops)


def test_rollback_restores_the_rows():
    db = make_db()
    before = {t: db.dump(t, skip=("updatedAt", "lastSyncedAt")) for t in ("Professor",)}
    run_enrich_load(db, lines(), EID, REG, apply=True, now=NOW)
    assert row(db, "p1")["openAlexId"]
    res = run_rollback(db, EID, apply=True, now=NOW + timedelta(hours=1))
    assert res["applied"]
    assert {t: db.dump(t, skip=("updatedAt", "lastSyncedAt")) for t in before} == before
    assert db.count("ProfessorSource") == 0 and db.count("SourceRecord") == 0
    assert plan_enrich_load(db, lines(), EID, REG, NOW + timedelta(hours=2)).summary["enrich:Professor"] == 2   # loadable again


def test_no_professor_is_ever_created():
    db = make_db()
    ghost = build_line(prof(id="nope", name="Ghost Person"), parse_author(raw_author("A9", "Person Ghost")), "r", {}, {"openAlexId": "url", "orcid": "url"}, NOW)
    n = db.count("Professor")
    res = run_enrich_load(db, [ghost], EID, REG, apply=True, now=NOW)
    assert db.count("Professor") == n and res["plan"]["skipped"] and "no longer exists" in res["plan"]["skipped"][0]
    assert not any(o.kind == "insert" and o.table == "Professor" for o in plan_enrich_load(db, lines(), EID, REG, NOW).ops)


def test_verify_ignores_the_hand_filled_qa_file_but_catches_edits_to_the_data(tmp_path, monkeypatch):
    from profinder_pipeline.enrich import run as R
    from profinder_pipeline.enrich.engine import sha256_text
    texts = {"enrichments.jsonl": '{"a": 1}\n', "review.csv": "x\n", "qa_sample.csv": "candidate_id,qa_result\nc1,\n"}
    for n, tx in texts.items():
        (tmp_path / n).write_text(tx, encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {n: {"sha256": sha256_text(tx)} for n, tx in texts.items()}}), encoding="utf-8")
    assert R.verify(tmp_path) == []
    (tmp_path / "qa_sample.csv").write_text("candidate_id,qa_result\nc1,ok\n", encoding="utf-8")      # the reviewer's edit
    assert R.verify(tmp_path) == []
    (tmp_path / "enrichments.jsonl").write_text('{"a": 2}\n', encoding="utf-8")                          # tampering with the lines
    assert R.verify(tmp_path) == ["enrichments.jsonl: checksum differs"]
