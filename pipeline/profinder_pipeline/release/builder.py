"""Release builder core (task 5.3, doc 01 section 7). Pure Python: candidate dicts in, JSONL lines + manifest + report text out.

A candidate is RELEASED when its effective decision is auto_accept / human_approved and the matcher gave it an action
(insert / enrich / propose). Everything else stays in the review queue. Line formats (Prisma column names inside `fields`):
  professors.jsonl etc.  {"action": insert|enrich, "candidate_id", "natural_key", "entity_id" (existing row or null), "fields": {...}, "research_areas": [...]}
  source_records.jsonl   {"table": "SourceRecord" | "ProfessorSource", "candidate_id", ...}
  proposals.jsonl        ChangeLog rows (changeType PROPOSED_UPDATE): conflicts and locked-row changes, nothing is written to the entity row
Dates and money (D7) never leave here unless a human approved the candidate."""
import hashlib
import json
import math
import random
from collections import Counter
from datetime import datetime
from typing import Any, Optional

from ..match.modes import WRITABLE_DECISIONS, effective_decision

RELEASE_ACTIONS = ("insert", "enrich", "propose")
FILES = {"university": "universities.jsonl", "professor": "professors.jsonl", "program": "programs.jsonl", "scholarship": "scholarships.jsonl"}
SOURCE_FILE, PROPOSAL_FILE = "source_records.jsonl", "proposals.jsonl"
D7_FIELDS = frozenset({"deadline", "openingDate", "scholarshipDeadline", "applicationDeadline", "tuition", "applicationFee"})
STAMP_FIELDS = ("verifiedAt", "lastVerifiedAt", "lastCheckedAt", "confidence")
VER_STATUS = {"verified": "VERIFIED", "needs_review": "PENDING_REVIEW", "unverified": "UNVERIFIED", "outdated": "POTENTIALLY_OUTDATED"}
ALREADY_RELEASED = ("approved", "loaded")


def _iso(v: Any) -> Optional[str]:
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else (str(v) if v is not None else None)


def _dec(v: Optional[str]) -> Any:
    return None if v is None else json.loads(v)


def effective(c: dict[str, Any]) -> str:
    return effective_decision(c["decision"], c.get("checks") or [])


def is_selected(c: dict[str, Any]) -> bool:
    return (effective(c) in WRITABLE_DECISIONS and c.get("action") in RELEASE_ACTIONS
            and c.get("release_status") not in ALREADY_RELEASED)


def source_type(c: dict[str, Any]) -> str:
    if c["entity_type"] != "university":
        return "OFFICIAL_UNIVERSITY"
    src = str((c.get("payload") or {}).get("dataSource") or "")
    return "REGISTRY_MOE" if "moe" in src else "REGISTRY_ROR" if "ror" in src else "OPENALEX"


def build_entry(c: dict[str, Any]) -> dict[str, Any]:
    """-> {"line", "sources": [...], "proposal", "dropped": [field, ...]} for one selected candidate."""
    payload = dict(c.get("payload") or {})
    human = c["decision"] == "human_approved"
    entity, action = c["entity_type"], c["action"]
    diff = [{"field": d["field"], "before": _dec(d.get("before")), "after": _dec(d.get("after")), "kind": d.get("kind", "enrich")}
            for d in (c.get("diff") or [])]
    dropped: list[str] = []
    out: dict[str, Any] = {"line": None, "sources": [], "proposal": None, "dropped": dropped}

    def keep(fields: dict[str, Any]) -> dict[str, Any]:
        kept = {}
        for k, v in fields.items():
            if k in D7_FIELDS and not human:
                dropped.append(k)
            else:
                kept[k] = v
        return kept

    areas: list[str] = []
    fields: dict[str, Any] = {}
    if action == "insert":
        fields = keep({k: v for k, v in payload.items() if v is not None and k != "researchAreas"})
        areas = list(payload.get("researchAreas") or [])
    elif action == "enrich":
        applied = [d for d in diff if d["kind"] in ("enrich", "status")]
        fields = keep({d["field"]: d["after"] for d in applied if d["field"] != "researchAreas"})
        for d in applied:
            if d["field"] == "researchAreas":
                areas = list(d["after"] or [])
        if any(d["kind"] == "status" for d in diff):
            fields.update({k: payload[k] for k in STAMP_FIELDS if payload.get(k) is not None})
        elif fields:
            if payload.get("lastCheckedAt") is not None:
                fields["lastCheckedAt"] = payload["lastCheckedAt"]
        if not fields and not areas:
            action = "propose"
    if action in ("insert", "enrich") and (fields or areas):
        out["line"] = {"action": action, "candidate_id": c["candidate_id"], "natural_key": c.get("natural_key"), "entity_id": c.get("matched_entity_id"),
                       "fields": fields, "research_areas": sorted(areas)}
        status = "verified" if human else c.get("proposed_status") or "needs_review"
        checked = _iso(c.get("fetched_at"))
        base = {"candidate_id": c["candidate_id"], "entityType": entity.upper(), "entityId": c.get("matched_entity_id")}
        out["sources"].append({"table": "SourceRecord", **base, "url": c["source_url"], "sourceType": source_type(c), "verificationStatus": VER_STATUS[status],
                               "checkedAt": checked, "verifiedAt": checked if status == "verified" else None, "confidence": c.get("confidence"),
                               "httpStatus": c.get("http_status"), "contentHash": c.get("content_hash")})
        if entity == "professor":
            out["sources"].append({"table": "ProfessorSource", **base, "source": "official_faculty_page", "externalId": c.get("natural_key"),
                                   "sourceUrl": c["source_url"], "fetchedAt": checked})
    props = [d for d in diff if d["kind"] == "propose"]
    if props:
        out["proposal"] = {"candidate_id": c["candidate_id"], "entityType": entity.upper(), "entityId": c.get("matched_entity_id"),
                           "changeType": "PROPOSED_UPDATE", "beforeJson": {d["field"]: d["before"] for d in props},
                           "afterJson": {d["field"]: d["after"] for d in props}, "sourceUrl": c["source_url"]}
    return out


def jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n" for r in rows)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_files(cands: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, Any]]:
    """-> ({file name: text}, stats). Deterministic: sorted by university, entity, label."""
    selected = sorted((c for c in cands if is_selected(c)), key=lambda c: (c.get("university_name") or "", c["entity_type"], c["candidate_id"]))
    files: dict[str, list[dict[str, Any]]] = {n: [] for n in (*FILES.values(), SOURCE_FILE, PROPOSAL_FILE)}
    counts: dict[str, Counter] = {e: Counter() for e in FILES}
    dropped: Counter = Counter()
    fields_touched: Counter = Counter()
    for c in selected:
        e = build_entry(c)
        dropped.update(e["dropped"])
        if e["line"]:
            files[FILES[c["entity_type"]]].append(e["line"])
            counts[c["entity_type"]][e["line"]["action"]] += 1
            fields_touched.update(e["line"]["fields"].keys())
            files[SOURCE_FILE].extend(e["sources"])
        if e["proposal"]:
            files[PROPOSAL_FILE].append(e["proposal"])
            counts[c["entity_type"]]["propose"] += 1
    stats = {"selected": len(selected), "counts": {k: dict(v) for k, v in counts.items()}, "d7_dropped": dict(dropped),
             "fields_touched": dict(fields_touched)}
    return {n: jsonl(rows) for n, rows in files.items()}, stats


def manifest(release_id: str, created_at: datetime, git_sha: Optional[str], config_hash: str, texts: dict[str, str],
             stats: dict[str, Any], universities: list[str], mode: str) -> dict[str, Any]:
    files = {n: {"sha256": sha256_text(t), "lines": t.count("\n")} for n, t in sorted(texts.items())}
    checksum = sha256_text("\n".join(f"{n}  {f['sha256']}" for n, f in files.items()))
    return {"release_id": release_id, "created_at": _iso(created_at), "git_sha": git_sha, "config_hash": config_hash, "write_mode": mode,
            "universities": sorted(universities), "sources": ["official_university_pages", "registries"], "files": files,
            "counts": stats["counts"], "d7_dropped": stats["d7_dropped"], "release_checksum": checksum}


# ------------------------------------------------------------------------------------------------ QA sample (doc 02 section 5.3)
def qa_size(n: int) -> int:
    return min(n, min(30, max(10, math.ceil(0.05 * n))))


def choose_qa(cands: list[dict[str, Any]], seed: str = "qa-v1") -> set[str]:
    """5 % of the AUTO-accepted selected rows, at least 10 and at most 30 per university and entity type (all of them if fewer)."""
    pools: dict[tuple[str, str], list[str]] = {}
    for c in cands:
        if is_selected(c) and c["decision"] == "auto_accept" and c.get("action") in ("insert", "enrich"):
            pools.setdefault((c.get("university_name") or "", c["entity_type"]), []).append(c["candidate_id"])
    chosen: set[str] = set()
    for key, ids in sorted(pools.items()):
        ids.sort()
        chosen.update(random.Random(f"{seed}|{key[0]}|{key[1]}").sample(ids, qa_size(len(ids))))
    return chosen


def qa_gate(cands: list[dict[str, Any]]) -> dict[str, Any]:
    """Professors: error rate <= 2 %; zero errors for universities, programs and scholarships (dates, fees, scholarships)."""
    rows = [c for c in cands if c.get("qa_sample")]
    pending = [c["candidate_id"] for c in rows if c.get("qa_result") is None]
    prof = [c for c in rows if c["entity_type"] == "professor" and c.get("qa_result")]
    prof_err = sum(1 for c in prof if c["qa_result"] == "error")
    other_err = sum(1 for c in rows if c["entity_type"] != "professor" and c.get("qa_result") == "error")
    rate = (prof_err / len(prof)) if prof else 0.0
    ok = not pending and rate <= 0.02 and other_err == 0
    return {"pass": ok, "sampled": len(rows), "pending": len(pending), "professor_checked": len(prof), "professor_errors": prof_err,
            "professor_error_rate": round(rate, 4), "other_errors": other_err}


def report_text(release_id: str, cands: list[dict[str, Any]], stats: dict[str, Any], man: dict[str, Any], qa: dict[str, Any]) -> str:
    sel = {c["candidate_id"] for c in cands if is_selected(c)}
    per: dict[tuple[str, str], Counter] = {}
    for c in cands:
        key = (c.get("university_name") or "?", c["entity_type"])
        n = per.setdefault(key, Counter())
        if c["candidate_id"] in sel:
            n["released:" + c["action"]] += 1
        elif c["decision"] in ("rejected", "human_rejected"):
            n["rejected"] += 1
        else:
            n["review"] += 1
    lines = [f"# Release {release_id}", "", f"Checksum `{man['release_checksum']}` · created {man['created_at']} · git {man['git_sha'] or 'n/a'} · mode {man['write_mode']}", "",
             "## Coverage per university", "", "| university | entity | insert | enrich | propose | needs review | rejected |", "|---|---|---|---|---|---|---|"]
    for (u, e), n in sorted(per.items()):
        lines.append(f"| {u} | {e} | {n['released:insert']} | {n['released:enrich']} | {n['released:propose']} | {n['review']} | {n['rejected']} |")
    reasons: Counter = Counter()
    failed: Counter = Counter()
    for c in cands:
        if c["decision"] in ("rejected", "human_rejected"):
            reasons[(c.get("reject_reason") or "?").split(":")[0]] += 1
        for ch in c.get("checks") or []:
            if not ch.get("passed", True):
                failed[ch.get("name", "?")] += 1
    lines += ["", "## Rejects by reason", ""] + [f"- {k}: {v}" for k, v in reasons.most_common()]
    lines += ["", "## Failed checks (all current candidates)", ""] + [f"- {k}: {v}" for k, v in failed.most_common()]
    lines += ["", "## Fields written or enriched", ""] + [f"- {k}: {v}" for k, v in sorted(stats["fields_touched"].items())]
    lines += ["", "## Dates / fees held back (D7)", "", (", ".join(f"{k}: {v}" for k, v in stats["d7_dropped"].items()) or "none")]
    lines += ["", "## QA gate", "", json.dumps(qa, ensure_ascii=False)]
    return "\n".join(lines) + "\n"
