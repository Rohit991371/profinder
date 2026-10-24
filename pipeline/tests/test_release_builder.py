"""Task 5.3: release builder core (pure Python)."""
import json
from datetime import datetime, timezone

from profinder_pipeline.release.builder import (build_entry, build_files, choose_qa, manifest, qa_gate, qa_size, report_text, sha256_text, is_selected)

T = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


from tests_release_helpers import cand


def test_insert_line_source_records_and_areas():
    e = build_entry(cand(1))
    line = e["line"]
    assert line["action"] == "insert" and line["fields"]["nameZh"] == "姓1" and "email" not in line["fields"] and "researchAreas" not in line["fields"]
    assert line["research_areas"] == ["Machine Learning"]
    kinds = [s["table"] for s in e["sources"]]
    assert kinds == ["SourceRecord", "ProfessorSource"]
    sr = e["sources"][0]
    assert sr["entityType"] == "PROFESSOR" and sr["verificationStatus"] == "VERIFIED" and sr["httpStatus"] == 200 and sr["verifiedAt"] == "2026-10-24T09:00:00+00:00"


def test_enrich_line_has_only_changed_fields_status_stamps_and_proposals_split():
    diff = [{"field": "position", "before": json.dumps("教授"), "after": json.dumps("Professor"), "kind": "enrich"},
            {"field": "email", "before": json.dumps("old@x.edu.cn"), "after": json.dumps("new@x.edu.cn"), "kind": "propose"},
            {"field": "dataStatus", "before": json.dumps("unverified"), "after": json.dumps("verified"), "kind": "status"},
            {"field": "researchAreas", "before": json.dumps(["Computer Science"]), "after": json.dumps(["Computer Science", "Databases"]), "kind": "enrich"}]
    pay = {"name": "A", "lastCheckedAt": "2026-10-24T09:00:00+00:00", "verifiedAt": "2026-10-24T09:00:00+00:00", "confidence": None}
    e = build_entry(cand(2, action="enrich", matched_entity_id="p9", payload=pay, diff=diff))
    assert e["line"]["entity_id"] == "p9" and e["line"]["fields"] == {"position": "Professor", "dataStatus": "verified",
                                                                     "lastCheckedAt": pay["lastCheckedAt"], "verifiedAt": pay["verifiedAt"]}
    assert e["line"]["research_areas"] == ["Computer Science", "Databases"]
    assert e["proposal"]["beforeJson"] == {"email": "old@x.edu.cn"} and e["proposal"]["afterJson"] == {"email": "new@x.edu.cn"} and e["proposal"]["changeType"] == "PROPOSED_UPDATE"


def test_propose_only_candidate_makes_a_proposal_and_no_row_line():
    diff = [{"field": "email", "before": json.dumps("a@x.cn"), "after": json.dumps("b@x.cn"), "kind": "propose"}]
    e = build_entry(cand(3, action="propose", matched_entity_id="p1", diff=diff))
    assert e["line"] is None and e["sources"] == [] and e["proposal"]["entityId"] == "p1"


def test_dates_and_money_are_dropped_unless_a_human_approved():
    pay = {"degree": "Master", "major": "CS", "deadline": "2026-12-01", "tuition": "30000", "officialName": "MSc", "verificationStatus": "VERIFIED"}
    auto = build_entry(cand(4, entity="program", payload=pay))
    assert "deadline" not in auto["line"]["fields"] and set(auto["dropped"]) == {"deadline", "tuition"} and auto["line"]["fields"]["officialName"] == "MSc"
    human = build_entry(cand(5, entity="program", payload=pay, decision="human_approved"))
    assert human["line"]["fields"]["deadline"] == "2026-12-01" and not human["dropped"]


def test_selection_rules():
    assert is_selected(cand(1)) and not is_selected(cand(2, decision="needs_review")) and not is_selected(cand(3, action=None))
    assert not is_selected(cand(4, action="noop")) and not is_selected(cand(5, decision="rejected"))
    assert is_selected(cand(6, decision="human_approved")) and not is_selected(cand(7, release_status="loaded")) and is_selected(cand(8, release_status="abandoned"))
    bad = [{"name": "M_unique_match", "passed": False, "blocking": True}]
    assert not is_selected(cand(9, checks=bad))


def test_files_are_deterministic_and_manifest_checksums_verify():
    cands = [cand(i) for i in range(5)] + [cand(9, decision="needs_review")]
    t1, s1 = build_files(cands)
    t2, _ = build_files(list(reversed(cands)))
    assert t1 == t2 and s1["selected"] == 5 and s1["counts"]["professor"] == {"insert": 5}
    assert t1["professors.jsonl"].count("\n") == 5 and t1["source_records.jsonl"].count("\n") == 10
    m = manifest("rid", T, "abc1234", "cfg", t1, s1, ["Tsinghua University"], "additive")
    assert m["files"]["professors.jsonl"]["sha256"] == sha256_text(t1["professors.jsonl"]) and len(m["release_checksum"]) == 64
    m2 = manifest("rid", T, "abc1234", "cfg", t1, s1, ["Tsinghua University"], "additive")
    assert m["release_checksum"] == m2["release_checksum"]
    assert "Coverage per university" in report_text("rid", cands, s1, m, qa_gate(cands))


def test_qa_sample_size_and_stability():
    assert [qa_size(n) for n in (3, 10, 100, 300, 1000)] == [3, 10, 10, 15, 30]
    cands = [cand(i) for i in range(120)] + [cand(500 + i, decision="human_approved") for i in range(20)]
    a, b = choose_qa(cands), choose_qa(list(reversed(cands)))
    assert a == b and len(a) == 10 and all(i.startswith("c") and int(i[1:]) < 120 for i in a)       # only auto-accepted rows are sampled


def test_qa_gate():
    rows = [cand(i, qa_sample=True, qa_result="ok") for i in range(60)]
    assert qa_gate(rows)["pass"]
    rows[0]["qa_result"] = "error"
    g = qa_gate(rows)
    assert g["pass"] and g["professor_errors"] == 1 and g["professor_error_rate"] == 0.0167       # 1 of 60 is within 2 %
    rows[0]["qa_result"] = "ok"
    rows[1]["qa_result"] = None
    assert not qa_gate(rows)["pass"] and qa_gate(rows)["pending"] == 1
    many = [cand(i, qa_sample=True, qa_result="ok") for i in range(40)]
    many[0]["qa_result"] = "error"
    assert not qa_gate(many)["pass"]                                         # 2.5 % > 2 %
    prog = [cand(100, entity="scholarship", qa_sample=True, qa_result="error")]
    assert not qa_gate(prog)["pass"]
