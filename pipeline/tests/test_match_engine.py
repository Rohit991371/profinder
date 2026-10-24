"""Tasks 5.1/5.2 glue: candidates -> matched plans, with fake loaders (no database)."""
from datetime import datetime, timedelta, timezone

from profinder_pipeline.match.engine import plan_candidates
from profinder_pipeline.match.universities import university_fields
from profinder_pipeline.utils.placeholders import PlaceholderRegistry

REG = PlaceholderRegistry.from_yaml()
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
UNIS = [{"id": "u1", "name": "Tsinghua University", "nameZh": "清华大学", "openAlexId": "I99", "agencyNumber": "10003",
         "officialUrl": "https://www.campuschina.org/", "province": "Beijing", "dataStatus": "needs_verification",
         "verificationStatus": "PENDING_REVIEW", "createdAt": T0, "updatedAt": T0, "lastSyncedAt": None},
        {"id": "u2", "name": "Fudan University", "nameZh": "复旦大学", "createdAt": T0, "updatedAt": T0}]
PROFS = {"u1": [{"id": "p1", "name": "Zhu Jun", "nameZh": "朱军", "email": None, "profileUrl": None, "position": "教授", "profileIsPersonal": False,
                 "dataStatus": "unverified", "verificationStatus": "UNVERIFIED", "createdAt": T0, "updatedAt": T0, "lastSyncedAt": None},
                {"id": "p2", "name": "Li Si", "nameZh": "李四", "createdAt": T0, "updatedAt": T0 + timedelta(days=3)},
                {"id": "p3", "name": "Li Si", "nameZh": "李四", "createdAt": T0, "updatedAt": T0}]}


def prof(name_zh, **kw):
    pay = {"name": "X Y", "nameZh": name_zh, "position": "Professor", "dataStatus": "verified", "verificationStatus": "VERIFIED", "researchAreas": []}
    pay.update(kw)
    return {"candidate_id": f"c-{name_zh}", "entity_type": "professor", "university_name": "Tsinghua University", "payload": pay,
            "proposed_status": "verified", "decision": "auto_accept", "checks": []}


def run(cands):
    return {r.candidate_id: r for r in plan_candidates(
        cands, universities=UNIS, load_rows=lambda e, uid: PROFS.get(uid, []) if e == "professor" else [],
        load_areas=lambda ids: {}, registry=REG)}


def test_insert_enrich_ambiguous_and_unresolved():
    res = run([prof("王新", email="wx@tsinghua.edu.cn"), prof("朱军", email="zj@tsinghua.edu.cn"), prof("李四"),
               dict(prof("张三"), university_name="Nowhere University")])
    assert res["c-王新"].action == "insert" and res["c-王新"].payload_patch == {"universityId": "u1"} and res["c-王新"].matched_id is None
    r = res["c-朱军"]
    assert r.action == "enrich" and r.matched_id == "p1" and {c.field: c.kind for c in r.changes}["position"] == "enrich"
    assert {c.field for c in r.changes} >= {"email", "position", "dataStatus", "verificationStatus"}
    amb = res["c-李四"]
    assert amb.action is None and not amb.check["passed"] and amb.effective == "needs_review"
    un = res["c-张三"]
    assert un.action is None and un.check["name"] == "M_university_resolved" and un.effective == "needs_review"


def test_university_candidate_matches_and_replaces_placeholder_url():
    pay = {"name": "Tsinghua University", "nameZh": "清华大学", "officialUrl": "https://www.tsinghua.edu.cn/", "province": "Beijing",
           "dataStatus": "verified", "verificationStatus": "VERIFIED"}
    c = {"candidate_id": "u", "entity_type": "university", "university_name": "Tsinghua University", "payload": pay,
         "proposed_status": "verified", "decision": "auto_accept", "checks": []}
    r = run([c])["u"]
    kinds = {x.field: x.kind for x in r.changes}
    assert r.matched_id == "u1" and r.action == "enrich" and kinds == {"officialUrl": "enrich", "dataStatus": "status", "verificationStatus": "status"}


def test_locked_existing_row_only_gets_proposals():
    UNIS_LOCK = [dict(UNIS[0], updatedAt=T0 + timedelta(days=5))]
    res = plan_candidates([prof("朱军", email="zj@tsinghua.edu.cn")], universities=UNIS_LOCK,
                          load_rows=lambda e, uid: PROFS["u1"][:1], load_areas=lambda ids: {}, registry=REG)
    assert res[0].action == "enrich"          # the PROFESSOR row is untouched; only the university row would be locked
    UNIS_P = UNIS
    pl = [dict(PROFS["u1"][0], updatedAt=T0 + timedelta(days=5))]
    res = plan_candidates([prof("朱军", email="zj@tsinghua.edu.cn")], universities=UNIS_P, load_rows=lambda e, uid: pl,
                          load_areas=lambda ids: {}, registry=REG)
    assert res[0].action == "propose" and res[0].locked and all(c.kind == "propose" for c in res[0].changes)


def test_insert_only_mode_changes_nothing_on_existing_rows():
    res = plan_candidates([prof("朱军", email="zj@tsinghua.edu.cn"), prof("王新")], universities=UNIS,
                          load_rows=lambda e, uid: PROFS["u1"], load_areas=lambda ids: {}, registry=REG, mode="insert-only")
    assert [r.action for r in res] == ["noop", "insert"] and not res[0].changes


def test_university_fields_from_registry_row():
    peking = {"name": "Peking University", "name_zh": "北京大学", "province": "Beijing", "city": "Beijing", "official_url": "https://www.pku.edu.cn/",
              "url_ok": True, "openalex_id": "I20231570", "ror_id": "02v51f717", "sources": ["moe", "ror", "openalex"],
              "flags": ["no_campuschina_match"], "status": "verified", "existing_agency_number": "10001"}
    f = university_fields(peking)
    assert f["decision"] == "auto_accept" and f["proposed"] == "verified" and f["payload"]["officialUrl"] == "https://www.pku.edu.cn/"
    assert f["payload"]["dataSource"] == "registry:moe+ror+openalex" and f["source_url"] == "https://www.pku.edu.cn/"
    weak = dict(peking, status="needs_review", url_ok=False, sources=["moe"])
    g = university_fields(weak)
    assert g["decision"] == "needs_review" and "officialUrl" not in g["payload"] and g["checks"][0][1] is False
    none = dict(weak, official_url=None, status="unverified", ror_id=None, openalex_id=None)
    assert university_fields(none)["source_url"].startswith("https://")


def test_university_and_program_rows_are_never_human_locked():
    ulock = [dict(UNIS[0], updatedAt=T0 + timedelta(days=30))]          # updated long after creation (scripts), no lastSyncedAt column
    pay = {"name": "Tsinghua University", "officialUrl": "https://www.tsinghua.edu.cn/", "dataStatus": "verified", "verificationStatus": "VERIFIED"}
    c = {"candidate_id": "u", "entity_type": "university", "university_name": "Tsinghua University", "payload": pay,
         "proposed_status": "verified", "decision": "auto_accept", "checks": []}
    r = plan_candidates([c], universities=ulock, load_rows=lambda e, u: [], load_areas=lambda i: {}, registry=REG)[0]
    assert r.action == "enrich" and not r.locked


def test_english_subsite_goes_to_review():
    m = {"name": "Beijing Jiaotong University", "name_zh": "北京交通大学", "official_url": "http://en.njtu.edu.cn/", "url_ok": True,
         "sources": ["moe", "ror"], "flags": [], "status": "verified"}
    f = university_fields(m)
    assert f["decision"] == "needs_review" and f["proposed"] == "needs_review" and any(c[0] == "S_english_subsite" for c in f["checks"])
    ok = dict(m, official_url="https://www.bjtu.edu.cn/")
    assert university_fields(ok)["decision"] == "auto_accept"


def test_shared_directory_url_and_import_rows():
    from profinder_pipeline.match.modes import shared_profile_urls
    shared = "https://www.cs.sjtu.edu.cn/en/gjjrc.html"
    rows = [{"id": f"s{i}", "name": f"N{i}", "nameZh": f"姓名{i}", "profileUrl": shared, "position": "Faculty", "dataSource": "official_import",
             "createdAt": T0, "updatedAt": T0 + timedelta(days=3), "lastSyncedAt": None, "dataStatus": "unverified", "verificationStatus": "UNVERIFIED"}
            for i in range(3)]
    assert shared_profile_urls(rows) == frozenset({"cs.sjtu.edu.cn/en/gjjrc.html"})
    c = prof("姓名1", profileUrl="https://www.cs.sjtu.edu.cn/jiaoshiml/a.html", position="Professor")
    c["university_name"] = "Shanghai Jiao Tong University"
    unis = UNIS + [{"id": "u9", "name": "Shanghai Jiao Tong University", "createdAt": T0, "updatedAt": T0}]
    kw = dict(universities=unis, load_rows=lambda e, uid: rows if uid == "u9" else [], load_areas=lambda i: {}, registry=REG)
    strict = plan_candidates([c], **kw)[0]
    assert strict.locked and strict.action == "propose"
    ok = plan_candidates([c], lock_policy="import-ok", **kw)[0]
    kinds = {x.field: x.kind for x in ok.changes}
    assert not ok.locked and kinds["profileUrl"] == "enrich" and kinds["dataStatus"] == "status"
    assert kinds.get("position") == "enrich"
