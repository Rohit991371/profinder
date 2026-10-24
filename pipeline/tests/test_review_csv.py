"""Task 5.4: review CSV round-trip and decision rules (pure Python; the record model is replaced by a stub validator)."""
from datetime import datetime, timezone

from profinder_pipeline.review.csvio import COLUMNS, evaluate_row, needs_human, read_csv, write_csv
from tests_release_helpers import cand

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
PAGE = "李铁英 职称：专任副研究员 邮箱：tyli@cs.ecnu.edu.cn 研究方向：软件工程"
B4 = {"name": "B4_school_from_page", "passed": False, "blocking": True, "detail": "school unknown"}


def ident(entity, payload):
    return payload


def row(**kw):
    base = {c: "" for c in COLUMNS}
    base.update({"candidate_id": "c001", "reviewer": "Kinza", "source_checked": "yes"})
    base.update(kw)
    return base


def review_cand(**kw):
    return cand(1, decision="needs_review", proposed_status="needs_review", checks=[B4],
                payload={"name": "Li Tieying", "nameZh": "李铁英", "school": None, "email": "tyli@cs.ecnu.edu.cn", "dataStatus": "needs_review",
                         "verificationStatus": "PENDING_REVIEW"}, **kw)


def test_csv_round_trip_with_chinese_and_bom(tmp_path):
    cands = [review_cand(), cand(2), cand(3, qa_sample=True)]
    n = write_csv(tmp_path / "r.csv", cands)
    assert n == 2                                                  # the needs_review one + the QA row; the plain auto_accept row is not listed
    raw = (tmp_path / "r.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = read_csv(tmp_path / "r.csv")
    assert {r["name_or_title"] for r in rows} == {"李铁英", "姓3"} and rows[0]["candidate_id"] and "B4_school_from_page" in "".join(r["failed_checks"] for r in rows)
    assert needs_human(review_cand()) and not needs_human(cand(2))


def test_approve_needs_source_checked_and_a_fix_for_the_blocking_check():
    c = review_cand()
    assert not evaluate_row(row(decision="approve", source_checked=""), c, PAGE, NOW, ident).ok
    r = evaluate_row(row(decision="approve"), c, PAGE, NOW, ident)
    assert not r.ok and "B4_school_from_page" in r.message
    ok = evaluate_row(row(decision="edit", edits='{"school": "School of Computer Science and Technology"}'), c, PAGE, NOW, ident)
    assert ok.ok and ok.update["decision"] == "human_approved" and ok.update["proposed_status"] == "verified"
    p = ok.update["payload"]
    assert p["school"].startswith("School of") and p["dataStatus"] == "verified" and p["verificationStatus"] == "VERIFIED" and p["verifiedAt"].startswith("2026-10-24")
    assert ok.update["extra_check"]["name"] == "H_human_review"


def test_override_needs_yes_and_a_note():
    c = review_cand()
    assert not evaluate_row(row(decision="approve", override="yes"), c, PAGE, NOW, ident).ok
    ok = evaluate_row(row(decision="approve", override="yes", note="listed on the CS school page"), c, PAGE, NOW, ident)
    assert ok.ok and "B4_school_from_page" in ok.message


def test_edited_email_must_be_in_the_page_text():
    c = review_cand()
    edits = '{"school": "S", "email": "other@cs.ecnu.edu.cn"}'
    bad = evaluate_row(row(decision="edit", edits=edits), c, PAGE, NOW, ident)
    assert not bad.ok and "email" in bad.message
    assert evaluate_row(row(decision="edit", edits=edits, override="yes", note="seen on the personal site"), c, PAGE, NOW, ident).ok
    assert evaluate_row(row(decision="edit", edits='{"school": "S", "email": "TYLI@cs.ecnu.edu.cn"}'), c, PAGE, NOW, ident).ok


def test_invalid_json_unknown_id_and_invalid_record():
    assert "valid JSON" in evaluate_row(row(decision="edit", edits="{school"), review_cand(), PAGE, NOW, ident).message
    assert "unknown candidate_id" in evaluate_row(row(decision="approve"), None, PAGE, NOW, ident).message

    def boom(entity, payload):
        raise ValueError("position: not a faculty role")
    r = evaluate_row(row(decision="edit", edits='{"school": "S"}'), review_cand(), PAGE, NOW, boom)
    assert not r.ok and "invalid" in r.message


def test_reject_needs_a_note_and_never_approves():
    assert not evaluate_row(row(decision="reject"), review_cand(), PAGE, NOW, ident).ok
    r = evaluate_row(row(decision="reject", note="visiting scholar"), review_cand(), PAGE, NOW, ident)
    assert r.ok and r.update["decision"] == "human_rejected" and "visiting scholar" in r.update["reject_reason"]


def test_ambiguous_match_cannot_be_approved():
    m = {"name": "M_unique_match", "passed": False, "blocking": True, "detail": "2 existing rows share this email"}
    c = cand(1, decision="auto_accept", checks=[m])
    r = evaluate_row(row(decision="approve", override="yes", note="x"), c, PAGE, NOW, ident)
    assert not r.ok and "ambiguous" in r.message


def test_qa_result_rows():
    c = cand(1, qa_sample=True)
    assert evaluate_row(row(qa_result="ok"), c, PAGE, NOW, ident).update["qa_result"] == "ok"
    assert not evaluate_row(row(qa_result="error"), c, PAGE, NOW, ident).ok                    # error needs a note
    e = evaluate_row(row(qa_result="error", note="wrong department"), c, PAGE, NOW, ident)
    assert e.ok and e.update["qa_result"] == "error"
    assert not evaluate_row(row(qa_result="maybe"), c, PAGE, NOW, ident).ok
    assert not evaluate_row(row(qa_result="ok", source_checked=""), c, PAGE, NOW, ident).ok
    none = evaluate_row(row(), c, PAGE, NOW, ident)
    assert not none.ok and "no decision" in none.message
