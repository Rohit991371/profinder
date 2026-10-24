"""Review round-trip core (task 5.4, doc 02 section 5.2). Pure Python (pydantic only through the injectable `validate`).

export: candidates that need a human + the QA sample  -> needs_review.csv (UTF-8 with BOM, opens in Excel)
import: reviewer columns -> one Outcome per row: an `update` for ingest.candidate, or an error message that explains why the row was refused
Rules: approve/edit need source_checked=yes; edits are re-validated by the record model and grounded in the page text (email, interests, lab,
personal website); a failed blocking check can only be overridden with override=yes AND a note; an ambiguous match (M_*) cannot be approved here."""
import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..release.builder import effective
from ..schemas.enums import EntityType, ProposedStatus
from ..schemas.status import TIMESTAMP_COLUMNS, status_pair, status_timestamps
from ..validate.grounding import norm_text, text_in

COLUMNS = ["candidate_id", "entity_type", "university", "name_or_title", "action", "source_url", "evidence", "failed_checks", "proposed_status",
           "field_changes", "qa_sample", "decision", "source_checked", "edits", "override", "note", "reviewer", "qa_result"]
GROUNDED_FIELDS = ("email", "researchInterests", "lab", "personalWebsite")
FIELD_CHECK_FIX = {"B4_school_from_page": "school"}      # a failed blocking check that a reviewer's edit can repair


def needs_human(c: dict[str, Any]) -> bool:
    return c["decision"] not in ("rejected", "human_rejected", "human_approved") and effective(c) == "needs_review"


def label(c: dict[str, Any]) -> str:
    p = c.get("payload") or {}
    return str(p.get("nameZh") or p.get("name") or f"{p.get('degree', '')} {p.get('major', '')}".strip() or "?")


def export_row(c: dict[str, Any]) -> dict[str, str]:
    ev = "; ".join(f"{e.get('field')}: {str(e.get('quote'))[:120]}" for e in (c.get("evidence") or []))
    failed = "; ".join(f"{k['name']}: {str(k.get('detail') or '')[:100]}" for k in (c.get("checks") or []) if not k.get("passed", True))
    changes = "; ".join(f"[{d.get('kind')}] {d['field']}: {d.get('before')} -> {d.get('after')}" for d in (c.get("diff") or []))
    return {"candidate_id": c["candidate_id"], "entity_type": c["entity_type"], "university": c.get("university_name") or "", "name_or_title": label(c),
            "action": c.get("action") or "unplanned", "source_url": c.get("source_url") or "", "evidence": ev, "failed_checks": failed,
            "proposed_status": c.get("proposed_status") or "", "field_changes": changes, "qa_sample": "yes" if c.get("qa_sample") else "",
            "decision": "", "source_checked": "", "edits": "", "override": "", "note": "", "reviewer": "", "qa_result": c.get("qa_result") or ""}


def write_csv(path: Path, cands: list[dict[str, Any]]) -> int:
    rows = [export_row(c) for c in cands if needs_human(c) or (c.get("qa_sample") and c["decision"] == "auto_accept")]
    rows.sort(key=lambda r: (r["university"], r["entity_type"], r["name_or_title"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


@dataclass
class Outcome:
    candidate_id: str
    ok: bool
    message: str
    update: dict[str, Any] = field(default_factory=dict)      # columns to write on ingest.candidate (payload replaces, checks appended)


def default_validate(entity: str, payload: dict[str, Any]) -> dict[str, Any]:
    from ..schemas.entities import RECORD_MODELS
    return RECORD_MODELS[EntityType(entity)].model_validate(payload).model_dump(mode="json")


def _fail(cid: str, msg: str) -> Outcome:
    return Outcome(cid, False, msg)


def evaluate_row(row: dict[str, str], cand: Optional[dict[str, Any]], page_text: Optional[str], now: datetime,
                 validate: Callable[[str, dict[str, Any]], dict[str, Any]] = default_validate) -> Outcome:
    cid = row.get("candidate_id", "")
    if cand is None:
        return _fail(cid, "unknown candidate_id (not in the CURRENT candidates: was `extract run` repeated after the export?)")
    decision, qa = row.get("decision", "").lower(), row.get("qa_result", "").lower()
    reviewer, note = row.get("reviewer", ""), row.get("note", "")
    checked = row.get("source_checked", "").lower() == "yes"
    upd: dict[str, Any] = {}
    if qa:
        if qa not in ("ok", "error"):
            return _fail(cid, f"qa_result must be ok or error, got '{qa}'")
        if not checked or not reviewer:
            return _fail(cid, "qa_result needs source_checked=yes and a reviewer")
        if qa == "error" and not note:
            return _fail(cid, "qa_result=error needs a note describing the error")
        upd.update({"qa_result": qa, "reviewer": reviewer, "reviewed_at": now, "review_note": note or None})
    if not decision:
        return Outcome(cid, bool(upd), "QA result recorded" if upd else "no decision (left as is)", upd)
    if decision not in ("approve", "edit", "reject"):
        return _fail(cid, f"decision must be approve / edit / reject, got '{decision}'")
    if not reviewer:
        return _fail(cid, "reviewer is required")
    if decision == "reject":
        if not note:
            return _fail(cid, "reject needs a note (the reason is kept)")
        upd.update({"decision": "human_rejected", "reject_reason": f"reviewer: {note}", "reviewer": reviewer, "reviewed_at": now, "review_note": note})
        return Outcome(cid, True, "rejected", upd)
    if not checked:
        return _fail(cid, "approve/edit are only allowed with source_checked=yes (open source_url first)")
    payload = dict(cand.get("payload") or {})
    edits: dict[str, Any] = {}
    if row.get("edits"):
        try:
            edits = json.loads(row["edits"])
        except json.JSONDecodeError as exc:
            return _fail(cid, f"edits is not valid JSON: {exc}")
        if not isinstance(edits, dict) or not edits:
            return _fail(cid, "edits must be a JSON object such as {\"school\": \"School of Computer Science\"}")
    if decision == "edit" and not edits:
        return _fail(cid, "decision=edit needs edits")
    override, overridden = row.get("override", "").lower() == "yes", []
    if edits:
        pn = norm_text(page_text or "")
        for k in GROUNDED_FIELDS:
            if edits.get(k) and not text_in(str(edits[k]), pn):
                if not (override and note):
                    return _fail(cid, f"edited {k} is not found in the page text: add override=yes and a note to accept it anyway")
                overridden.append(f"grounding:{k}")
        payload.update(edits)
    failed = [k for k in (cand.get("checks") or []) if not k.get("passed", True) and k.get("blocking", True)]
    if any(k["name"].startswith("M_") for k in failed):
        return _fail(cid, "ambiguous or unresolved match (M_*): resolve the duplicate rows first (legacy audit) or reject this candidate")
    for k in failed:
        if edits.get(FIELD_CHECK_FIX.get(k["name"], "\0")):
            continue
        if not (override and note):
            return _fail(cid, f"blocking check {k['name']} failed ({k.get('detail')}): fix it with edits, or override=yes with a note")
        overridden.append(k["name"])
    entity = cand["entity_type"]
    pair = status_pair(EntityType(entity), ProposedStatus.verified)
    fetched = cand.get("fetched_at") or now
    payload.update({"dataStatus": pair.data_status.value if pair.data_status else payload.get("dataStatus"),
                    "verificationStatus": pair.verification_status.value})
    payload.update({k: v.isoformat() for k, v in status_timestamps(EntityType(entity), ProposedStatus.verified, fetched, now).items()})
    if pair.data_status is None:
        payload.pop("dataStatus", None)
    try:
        payload = validate(entity, payload)
    except Exception as exc:  # noqa: BLE001  (pydantic ValidationError)
        return _fail(cid, f"edited record is invalid: {str(exc)[:300]}")
    detail = f"{decision} by {reviewer}" + (f"; edits: {sorted(edits)}" if edits else "") + (f"; overridden: {overridden}" if overridden else "")
    upd.update({"payload": payload, "decision": "human_approved", "proposed_status": "verified", "reviewer": reviewer, "reviewed_at": now,
                "review_note": note or None, "extra_check": {"name": "H_human_review", "passed": True, "blocking": False, "detail": detail}})
    return Outcome(cid, True, detail, upd)
