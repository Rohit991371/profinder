"""Portable approval of a release bundle (Iteration 6, handover).

`release approve` records the approval INSIDE the bundle (approval.json: release id, bundle checksum, who, when, QA gate result), because the
person who builds and approves a release and the owner who loads it use different databases. `release import` on the owner's side verifies the
checksums and the approval file, then registers the bundle as `approved` in the owner's ingest.release, which is what `load` requires.
Pure Python (no psycopg): the connection is passed in."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

APPROVAL = "approval.json"


def make_approval(man: dict, by: str, gate: dict, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"release_id": man["release_id"], "release_checksum": man["release_checksum"], "approved_by": by,
            "approved_at": now.isoformat(timespec="seconds"), "qa": gate}


def write_approval(folder: Path, man: dict, by: str, gate: dict, now: Optional[datetime] = None) -> dict:
    appr = make_approval(man, by, gate, now)
    (folder / APPROVAL).write_text(json.dumps(appr, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return appr


def check_approval(folder: Path, man: dict) -> list:
    """Problems with approval.json (empty list = it belongs to exactly this bundle and names an approver)."""
    p = folder / APPROVAL
    if not p.exists():
        return [f"{APPROVAL} is missing: the release was never approved with `release approve`"]
    try:
        a = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return [f"{APPROVAL} is not valid JSON"]
    problems = []
    if a.get("release_id") != man.get("release_id"):
        problems.append("approval is for another release id")
    if a.get("release_checksum") != man.get("release_checksum"):
        problems.append("approval was given for a different bundle checksum (the bundle changed after approval)")
    if not str(a.get("approved_by") or "").strip():
        problems.append("approval names nobody")
    if not (a.get("qa") or {}).get("pass"):
        problems.append("approval does not record a passed QA gate")
    return problems


def register(conn: Any, folder: Path, now: Optional[datetime] = None) -> str:
    """Insert or update the release row as `approved` (never lowers `loaded`). Returns the status the row has afterwards."""
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    a = json.loads((folder / APPROVAL).read_text(encoding="utf-8"))
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        "INSERT INTO ingest.release (release_id, created_at, manifest, status, approved_by, approved_at) "
        "VALUES (%s::uuid, %s, %s::jsonb, 'approved', %s, %s) "
        "ON CONFLICT (release_id) DO UPDATE SET manifest = EXCLUDED.manifest, approved_by = EXCLUDED.approved_by, approved_at = EXCLUDED.approved_at, "
        "status = CASE WHEN ingest.release.status = 'loaded' THEN 'loaded' ELSE 'approved' END RETURNING status",
        (man["release_id"], now, json.dumps(man, ensure_ascii=False), a["approved_by"], a["approved_at"])).fetchone()
    return row[0] if row else "approved"
