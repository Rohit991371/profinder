"""Handover: the approval travels inside the bundle; the owner registers it with `release import`."""
import json
from datetime import datetime, timezone

from profinder_pipeline.release.approval import APPROVAL, check_approval, make_approval, register, write_approval

MAN = {"release_id": "da464f5e-0000-4000-8000-000000000001", "release_checksum": "ab" * 32, "files": {}}
GATE = {"pass": True, "sampled": 117, "errors": 0}
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def folder_with(tmp_path, man=MAN):
    (tmp_path / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return tmp_path


def test_a_matching_approval_has_no_problems(tmp_path):
    f = folder_with(tmp_path)
    write_approval(f, MAN, "Rohit Gupta", GATE, NOW)
    assert check_approval(f, MAN) == []
    assert json.loads((f / APPROVAL).read_text())["approved_at"] == "2026-11-02T09:00:00+00:00"


def test_missing_wrong_or_stale_approvals_are_refused(tmp_path):
    f = folder_with(tmp_path)
    assert "missing" in check_approval(f, MAN)[0]
    write_approval(f, MAN, "A", GATE, NOW)
    changed = {**MAN, "release_checksum": "cd" * 32}                       # the bundle was rebuilt after approval
    assert any("different bundle checksum" in p for p in check_approval(f, changed))
    assert any("another release id" in p for p in check_approval(f, {**MAN, "release_id": "x"}))
    write_approval(f, MAN, "  ", GATE, NOW)
    assert any("names nobody" in p for p in check_approval(f, MAN))
    write_approval(f, MAN, "A", {"pass": False}, NOW)
    assert any("QA gate" in p for p in check_approval(f, MAN))
    (f / APPROVAL).write_text("{broken", encoding="utf-8")
    assert check_approval(f, MAN) == ["approval.json is not valid JSON"]


class FakeConn:
    def __init__(self, status="approved"):
        self.status, self.calls = status, []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        st = self.status
        return type("R", (), {"fetchone": lambda s: (st,)})()


def test_register_upserts_as_approved_and_never_lowers_loaded(tmp_path):
    f = folder_with(tmp_path)
    write_approval(f, MAN, "Rohit Gupta", GATE, NOW)
    c = FakeConn()
    assert register(c, f, NOW) == "approved"
    sql, params = c.calls[0]
    assert "ON CONFLICT (release_id) DO UPDATE" in sql and "WHEN ingest.release.status = 'loaded' THEN 'loaded'" in sql
    assert params[0] == MAN["release_id"] and params[3] == "Rohit Gupta" and json.loads(params[2])["release_checksum"] == MAN["release_checksum"]
    assert register(FakeConn("loaded"), f, NOW) == "loaded"


def test_make_approval_is_deterministic():
    assert make_approval(MAN, "A", GATE, NOW) == make_approval(MAN, "A", GATE, NOW)
