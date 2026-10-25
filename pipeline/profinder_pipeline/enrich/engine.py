"""Enrichment engine (task 6.1): which professors are eligible, what an enrichment line contains, and how a bundle is loaded.

Lines are NOT ingest.candidate rows on purpose: `ingest.current_candidate` shows only the newest run per university and entity type,
so an enrichment run stored as candidates would hide the extraction candidates of the same university. An enrichment bundle is its
own folder (output/enrich/<id>/) with a manifest; the loader reuses the release loader's operations, write modes, guards and
rollback (rollback works from ChangeLog `_release`, which here is the enrichment id).
Only these Professor columns are ever written, and only when empty/placeholder (else a proposal): openAlexId, orcid, publicationCount."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from ..load.loader import (CHANGE_ENRICHED, CHANGE_PROPOSED, LoadAbort, LoadPlan, Op, apply_ops, cuid, jdump, to_dt, user_counts)
from ..match.modes import plan_record, row_locked
from ..utils.placeholders import PlaceholderRegistry
from .authors import Author, bare_orcid

OFFICIAL_SOURCES = ("official_faculty_page", "official_import")
ENRICH_FIELDS = ("openAlexId", "orcid", "publicationCount")
SOURCE_NAME = "openalex"


# ------------------------------------------------------------------------------------------------ formats of existing values
def detect_url_style(values: list) -> str:
    """'url' if most existing non-empty values start with http, else 'bare'. Keeps new values in the style the app already uses."""
    vals = [v for v in values if isinstance(v, str) and v.strip()]
    if not vals:
        return "url"
    urls = sum(1 for v in vals if v.strip().lower().startswith("http"))
    return "url" if urls * 2 >= len(vals) else "bare"


def format_openalex_id(a: Author, style: str) -> str:
    return a.url if style == "url" else a.openalex_id


def format_orcid(orcid: str, style: str) -> str:
    return f"https://orcid.org/{orcid}" if style == "url" else orcid


def eligible(row: dict) -> Optional[str]:
    """None if the professor may be enriched, else the reason why not. OpenAlex never creates a professor and never enriches
    a row that came from OpenAlex itself or from nowhere (those are the legacy audit's business)."""
    if row.get("dataSource") not in OFFICIAL_SOURCES:
        return "not_official_sourced"
    if row.get("openAlexId"):
        return "already_has_openalex_id"
    return None


# ------------------------------------------------------------------------------------------------ line
def line_id(professor_id: str, author_id: str) -> str:
    return hashlib.md5(f"{professor_id}|{author_id}".encode()).hexdigest()


def build_line(prof: dict, author: Author, reason: str, checks: dict, styles: dict, fetched_at: datetime) -> dict:
    fields: dict[str, Any] = {"openAlexId": format_openalex_id(author, styles["openAlexId"])}
    if author.orcid and not bare_orcid(prof.get("orcid")):
        fields["orcid"] = format_orcid(author.orcid, styles["orcid"])
    if author.works_count is not None:
        fields["publicationCount"] = author.works_count
    return {"candidate_id": line_id(str(prof["id"]), author.openalex_id), "professor_id": str(prof["id"]), "university_id": prof.get("universityId"),
            "name": prof.get("name"), "nameZh": prof.get("nameZh"), "author_id": author.openalex_id, "author_name": author.name,
            "author_url": author.url, "reason": reason, "checks": checks, "fields": fields,
            "fetched_at": fetched_at.isoformat(timespec="seconds")}


def jsonl(rows: list) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n" for r in rows)


def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------------ load planning
def plan_enrich_load(db: Any, lines: list, enrich_id: str, registry: PlaceholderRegistry, now: datetime, lock_policy: str = "strict") -> LoadPlan:
    plan = LoadPlan()
    now_naive = to_dt(now)
    cols: dict[str, set] = {}

    def columns(t: str) -> set:
        if t not in cols:
            cols[t] = db.columns(t)
        return cols[t]

    def usable(table: str, values: dict) -> dict:
        out = {}
        for k, v in values.items():
            if k in columns(table):
                out[k] = to_dt(v) if k.endswith("At") else v
            else:
                plan.dropped_columns[f"{table}.{k}"] += 1
        return out

    for line in lines:
        pid, cid = line["professor_id"], line["candidate_id"]
        rows = db.rows('SELECT * FROM "Professor" WHERE id = %s', (pid,))
        if not rows:
            plan.skipped.append(f"Professor {pid}: row no longer exists")
            continue
        row = rows[0]
        if row.get("dataSource") not in OFFICIAL_SOURCES:
            plan.skipped.append(f"Professor {pid}: not official-sourced (dataSource={row.get('dataSource')!r})")
            continue
        locked = row_locked("Professor", row, lock_policy)
        payload = {k: v for k, v in line["fields"].items() if k in ENRICH_FIELDS and k in columns("Professor")}
        p = plan_record("Professor", payload, row, registry=registry, locked=locked)
        enrich = {c.field: c for c in p.changes if c.kind == "enrich"}
        propose = {c.field: c for c in p.changes if c.kind == "propose"}
        if propose:
            wanted = {f: c.after for f, c in propose.items()}
            exists = False
            for h in db.rows('SELECT "afterJson" FROM "ChangeLog" WHERE "entityType" = %s AND "entityId" = %s AND "changeType" = %s',
                             ("PROFESSOR", pid, CHANGE_PROPOSED)):
                try:
                    old = {k: v for k, v in json.loads(h["afterJson"] or "{}").items() if not k.startswith("_")}
                except ValueError:
                    old = {}
                exists = exists or old == wanted
            if not exists:
                plan.ops.append(Op("ChangeLog", "insert", usable("ChangeLog", {
                    "id": cuid(enrich_id, cid, "ChangeLog", CHANGE_PROPOSED), "entityType": "PROFESSOR", "entityId": pid, "changeType": CHANGE_PROPOSED,
                    "beforeJson": jdump({f: c.before for f, c in propose.items()}),
                    "afterJson": jdump({**wanted, "_release": enrich_id, "_candidate": cid}),
                    "sourceUrl": line["author_url"], "detectedAt": now_naive})))
                plan.summary["insert:ChangeLog(proposal)"] += 1
        if not enrich:
            plan.summary["noop:Professor"] += 1
            continue
        vals = {f: c.after for f, c in enrich.items()}
        plan.ops.append(Op("Professor", "update", usable("Professor", {**vals, "updatedAt": now_naive, "lastSyncedAt": now_naive}), where_id=pid,
                           guard_updated_at=row.get("updatedAt"), note=f"enrich {row.get('nameZh') or row.get('name')}"))
        plan.summary["enrich:Professor"] += 1
        src_meta = []
        have = db.rows('SELECT source, "externalId" FROM "ProfessorSource" WHERE "professorId" = %s', (pid,))
        if not any(h["source"] == SOURCE_NAME and h["externalId"] == line["author_id"] for h in have):
            sid = cuid(enrich_id, cid, "ProfessorSource")
            plan.ops.append(Op("ProfessorSource", "insert", usable("ProfessorSource", {
                "id": sid, "professorId": pid, "source": SOURCE_NAME, "externalId": line["author_id"], "sourceUrl": line["author_url"],
                "fetchedAt": line.get("fetched_at") or now_naive})))
            plan.summary["insert:ProfessorSource"] += 1
            src_meta.append({"table": "ProfessorSource", "id": sid})
        haves = db.rows('SELECT url FROM "SourceRecord" WHERE "entityType" = %s AND "entityId" = %s', ("PROFESSOR", pid))
        if not any(h["url"] == line["author_url"] for h in haves):
            sid = cuid(enrich_id, cid, "SourceRecord")
            plan.ops.append(Op("SourceRecord", "insert", usable("SourceRecord", {
                "id": sid, "entityType": "PROFESSOR", "entityId": pid, "url": line["author_url"], "sourceType": "OPENALEX",
                "verificationStatus": "UNVERIFIED", "checkedAt": line.get("fetched_at"), "createdAt": now_naive, "updatedAt": now_naive})))
            plan.summary["insert:SourceRecord"] += 1
            src_meta.append({"table": "SourceRecord", "id": sid})
        plan.ops.append(Op("ChangeLog", "insert", usable("ChangeLog", {
            "id": cuid(enrich_id, cid, "ChangeLog", CHANGE_ENRICHED), "entityType": "PROFESSOR", "entityId": pid, "changeType": CHANGE_ENRICHED,
            "beforeJson": jdump({k: row.get(k) for k in vals}),
            "afterJson": jdump({**vals, "_release": enrich_id, "_candidate": cid, "_areas_added": [], "_sources": src_meta}),
            "sourceUrl": line["author_url"], "detectedAt": now_naive})))
        plan.summary["insert:ChangeLog"] += 1
    return plan


def run_enrich_load(db: Any, lines: list, enrich_id: str, registry: PlaceholderRegistry, apply: bool, now: Optional[datetime] = None,
                    lock_policy: str = "strict") -> dict:
    now = now or datetime.now(timezone.utc)
    before = user_counts(db)
    plan = plan_enrich_load(db, lines, enrich_id, registry, now, lock_policy)
    result: dict[str, Any] = {"plan": plan.as_json(), "applied": False, "user_tables": before}
    if not apply:
        return result
    done = apply_ops(db, plan.ops)
    again = plan_enrich_load(db, lines, enrich_id, registry, now, lock_policy)
    if again.ops:
        raise LoadAbort(f"enrichment is not idempotent: a second plan still has {len(again.ops)} operations; nothing is committed")
    if user_counts(db) != before:
        raise LoadAbort("user tables changed; nothing is committed")
    result.update({"applied": True, "done": dict(done)})
    return result


def summarize(outcomes: list) -> Counter:
    c: Counter = Counter()
    for o in outcomes:
        c[f"{o['status']}:{o['reason']}"] += 1
    return c
