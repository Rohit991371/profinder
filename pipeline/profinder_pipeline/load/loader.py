"""Loader core (task 5.5, doc 01 section 4 S9 and doc 03 section 3). Pure logic over a `Db` wrapper.

plan_load(...) reads the release files and the CURRENT app rows and returns the exact operations; dry-run = print the plan, apply = execute
the same operations in one transaction, re-plan inside the transaction (it must be empty: idempotency) and compare the user tables.
Every line is re-planned with the same write-mode engine as the matcher, against fresh data: a field that was filled meanwhile is not
overwritten, a human-touched row only gets proposals, an ambiguous or vanished row is skipped and reported. Nothing is ever deleted here."""
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..match.matchers import ProfessorMatcher, ProgramMatcher, ScholarshipMatcher, UniversityMatcher
from ..match.modes import _norm, plan_record, row_locked, shared_profile_urls
from ..release.builder import FILES, PROPOSAL_FILE, SOURCE_FILE
from ..utils.placeholders import PlaceholderRegistry

MODEL = {"university": "University", "professor": "Professor", "program": "Program", "scholarship": "Scholarship"}
ORDER = ("university", "professor", "program", "scholarship")
STAMPS = ("verifiedAt", "lastVerifiedAt", "lastCheckedAt", "confidence")
STATUS_BACK = {"VERIFIED": "verified", "PENDING_REVIEW": "needs_review", "UNVERIFIED": "unverified", "POTENTIALLY_OUTDATED": "outdated"}
USER_TABLES = ("Student", "Application", "SavedProfessor", "Subscription", "UsageCounter", "BillingEvent", "ApplyRequest", "ApplyRequestItem",
               "ApplyDocument", "MatchFeedback")
CHANGE_CREATED, CHANGE_ENRICHED, CHANGE_PROPOSED, CHANGE_ROLLED_BACK = "PIPELINE_CREATED", "PIPELINE_ENRICHED", "PROPOSED_UPDATE", "PIPELINE_ROLLED_BACK"


class LoadAbort(RuntimeError):
    pass


def cuid(*parts: str) -> str:
    """Deterministic 25-character id shaped like a Prisma cuid (the same release gives the same ids: dry-run shows what apply writes)."""
    return "c" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


def to_dt(v: Any) -> Any:
    """ISO text -> naive UTC datetime (Prisma DateTime columns are `timestamp(3)` without time zone)."""
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
        return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
    if isinstance(v, datetime) and v.tzinfo:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


def jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o))


@dataclass
class Op:
    table: str
    kind: str                                  # insert | update | link | delete (delete only from rollback.py)
    values: dict[str, Any]
    where_id: Optional[str] = None
    guard_updated_at: Any = None               # update only if nobody touched the row since we read it
    note: str = ""


@dataclass
class LoadPlan:
    ops: list[Op] = field(default_factory=list)
    summary: Counter = field(default_factory=Counter)
    skipped: list[str] = field(default_factory=list)
    dropped_columns: Counter = field(default_factory=Counter)

    def writes(self) -> int:
        return len(self.ops)

    def as_json(self) -> dict[str, Any]:
        return {"summary": dict(sorted(self.summary.items())), "skipped": self.skipped[:200], "skipped_total": len(self.skipped),
                "dropped_columns": dict(self.dropped_columns), "operations": len(self.ops)}


@dataclass
class ReleaseData:
    lines: dict[str, list[dict[str, Any]]]
    sources: dict[str, list[dict[str, Any]]]
    proposals: list[dict[str, Any]]


def read_release(folder: Path) -> ReleaseData:
    def rd(name: str) -> list[dict[str, Any]]:
        p = folder / name
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in rd(SOURCE_FILE):
        sources[s["candidate_id"]].append(s)
    return ReleaseData({e: rd(FILES[e]) for e in ORDER}, dict(sources), rd(PROPOSAL_FILE))


def plan_load(db: Any, rel: ReleaseData, release_id: str, registry: PlaceholderRegistry, now: datetime, lock_policy: str = "strict") -> LoadPlan:
    plan = LoadPlan()
    now_naive = to_dt(now)
    cols: dict[str, set[str]] = {}

    def columns(t: str) -> set[str]:
        if t not in cols:
            cols[t] = db.columns(t)
        return cols[t]

    def usable(table: str, values: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in values.items():
            if k in columns(table):
                out[k] = to_dt(v) if k.endswith("At") else v
            else:
                plan.dropped_columns[f"{table}.{k}"] += 1
        return out

    universities = db.rows('SELECT * FROM "University"')
    um = UniversityMatcher(universities)
    area_id = {r["name"]: r["id"] for r in db.rows('SELECT id, name FROM "ResearchArea"')}
    per_uni: dict[tuple[str, str], tuple[dict[str, dict[str, Any]], Any, frozenset]] = {}
    areas_by_uni: dict[str, dict[str, set[str]]] = {}
    row_by_id: dict[tuple[str, str], Optional[dict[str, Any]]] = {}
    props: dict[str, dict[str, Any]] = {}          # candidate_id -> proposal record
    touched: dict[str, tuple[str, str]] = {}       # candidate_id -> (entity, row id) for rows written or already present

    def uni_rows(entity: str, uid: str):
        if (entity, uid) not in per_uni:
            rows = db.rows(f'SELECT * FROM "{MODEL[entity]}" WHERE "universityId" = %s', (uid,))
            m = {"professor": ProfessorMatcher, "program": ProgramMatcher, "scholarship": ScholarshipMatcher}[entity](rows)
            per_uni[(entity, uid)] = ({str(r["id"]): r for r in rows}, m, shared_profile_urls(rows) if entity == "professor" else frozenset())
            if entity == "professor":
                a: dict[str, set[str]] = defaultdict(set)
                for r in db.rows('SELECT pra."professorId" AS pid, ra.name AS name FROM "ProfessorResearchArea" pra JOIN "ResearchArea" ra ON ra.id = pra."researchAreaId" '
                                 'JOIN "Professor" p ON p.id = pra."professorId" WHERE p."universityId" = %s', (uid,)):
                    a[r["pid"]].add(r["name"])
                areas_by_uni[uid] = a
        return per_uni[(entity, uid)]

    def fetch_row(entity: str, rid: str) -> Optional[dict[str, Any]]:
        key = (entity, rid)
        if key not in row_by_id:
            r = db.rows(f'SELECT * FROM "{MODEL[entity]}" WHERE id = %s', (rid,))
            row_by_id[key] = r[0] if r else None
        return row_by_id[key]

    for entity in ORDER:
        for line in rel.lines[entity]:
            cid, fields, model = line["candidate_id"], dict(line["fields"]), MODEL[entity]
            status = STATUS_BACK.get(str(fields.get("verificationStatus")))
            payload = dict(fields)
            payload["researchAreas"] = list(line.get("research_areas") or [])
            existing: Optional[dict[str, Any]] = None
            uid = fields.get("universityId")
            if line.get("entity_id"):
                existing = fetch_row(entity, line["entity_id"])
                if existing is None:
                    plan.skipped.append(f"{model} {line['entity_id']}: row no longer exists ({cid[:8]})")
                    continue
                uid = existing["id"] if entity == "university" else existing.get("universityId")
            elif entity == "university":
                m = um.match(fields)
                existing = um.by_id.get(m.entity_id) if m.status == "unique" else None
                if existing is None:
                    plan.skipped.append(f"University {fields.get('name')}: no unique existing row ({m.status})")
                    continue
            else:
                if uid not in um.by_id:
                    plan.skipped.append(f"{model} {fields.get('nameZh') or fields.get('name')}: university {uid} not found ({cid[:8]})")
                    continue
                rows, matcher, _ = uni_rows(entity, uid)
                m = matcher.match(fields)
                if m.status == "ambiguous":
                    plan.skipped.append(f"{model} {fields.get('nameZh') or fields.get('name')}: ambiguous existing rows at load time ({cid[:8]})")
                    continue
                existing = rows.get(m.entity_id) if m.status == "unique" else None
            shared = uni_rows(entity, uid)[2] if (entity == "professor" and uid) else frozenset()
            areas = areas_by_uni.get(uid, {}).get(str(existing["id"]), set()) if (existing and entity == "professor") else None
            locked = bool(existing) and row_locked(model, existing, lock_policy)
            p = plan_record(model, payload, existing, registry=registry, proposed_status=status, status_writable=True, locked=locked,
                            existing_areas=areas, shared_urls=shared)
            if existing is None:
                rid = cuid(release_id, cid, model)
                vals = {k: v for k, v in fields.items()}
                vals.update({"id": rid, "createdAt": now_naive, "updatedAt": now_naive})
                if entity == "professor":
                    vals["lastSyncedAt"] = now_naive
                plan.ops.append(Op(model, "insert", usable(model, vals), note=f"insert {fields.get('nameZh') or fields.get('name')}"))
                plan.summary[f"insert:{model}"] += 1
                changed_after, changed_before, added_areas = dict(fields), {}, list(payload["researchAreas"])
                kind_log = CHANGE_CREATED
            else:
                rid = str(existing["id"])
                enrich = [c for c in p.changes if c.kind in ("enrich", "status") and c.field != "researchAreas"]
                added_areas = []
                for c in p.changes:
                    if c.field == "researchAreas" and c.kind == "enrich":
                        added_areas = [a for a in c.after if a not in (areas or set())]
                for c in p.changes:
                    if c.kind == "propose" and c.field != "researchAreas":
                        d = props.setdefault(cid, {"candidate_id": cid, "entityType": model.upper(), "entityId": rid, "fields": {}})
                        d["fields"][c.field] = (c.before, c.after)
                if not enrich and not added_areas:
                    plan.summary[f"noop:{model}"] += 1
                    touched[cid] = (entity, rid)
                    continue
                vals = {c.field: c.after for c in enrich}
                if any(c.kind == "status" for c in enrich):
                    vals.update({k: fields[k] for k in STAMPS if fields.get(k) is not None})
                elif "lastCheckedAt" in fields:
                    vals["lastCheckedAt"] = fields["lastCheckedAt"]
                changed_after = dict(vals)
                changed_before = {k: existing.get(k) for k in vals}
                vals_op = dict(vals)
                vals_op["updatedAt"] = now_naive
                if entity == "professor":
                    vals_op["lastSyncedAt"] = now_naive
                plan.ops.append(Op(model, "update", usable(model, vals_op), where_id=rid, guard_updated_at=existing.get("updatedAt"),
                                   note=f"enrich {fields.get('nameZh') or fields.get('name') or rid}"))
                plan.summary[f"enrich:{model}"] += 1
                kind_log = CHANGE_ENRICHED
            touched[cid] = (entity, rid)
            link_ids = []
            if entity == "professor":
                for a in added_areas:
                    if a in area_id:
                        plan.ops.append(Op("ProfessorResearchArea", "link", {"professorId": rid, "researchAreaId": area_id[a]}))
                        plan.summary["link:ProfessorResearchArea"] += 1
                    else:
                        plan.skipped.append(f"research area '{a}' is not in ResearchArea ({cid[:8]})")
            src_meta = []
            for s in rel.sources.get(cid, []):
                sid = cuid(release_id, cid, s["table"])
                if s["table"] == "SourceRecord":
                    have = db.rows('SELECT url, "contentHash" FROM "SourceRecord" WHERE "entityType" = %s AND "entityId" = %s', (s["entityType"], rid)) if existing else []
                    if any(h["url"] == s["url"] and h["contentHash"] == s.get("contentHash") for h in have):
                        continue
                    vals = {"id": sid, "entityType": s["entityType"], "entityId": rid, "url": s["url"], "sourceType": s["sourceType"],
                            "verificationStatus": s["verificationStatus"], "checkedAt": s.get("checkedAt"), "verifiedAt": s.get("verifiedAt"),
                            "confidence": s.get("confidence"), "httpStatus": s.get("httpStatus"), "contentHash": s.get("contentHash"),
                            "createdAt": now_naive, "updatedAt": now_naive}
                else:
                    have = db.rows('SELECT source, "externalId" FROM "ProfessorSource" WHERE "professorId" = %s', (rid,)) if existing else []
                    if any(h["source"] == s["source"] and h["externalId"] == s.get("externalId") for h in have):
                        continue
                    vals = {"id": sid, "professorId": rid, "source": s["source"], "externalId": s.get("externalId"), "sourceUrl": s.get("sourceUrl"),
                            "fetchedAt": s.get("fetchedAt") or now_naive}
                plan.ops.append(Op(s["table"], "insert", usable(s["table"], vals)))
                plan.summary[f"insert:{s['table']}"] += 1
                src_meta.append({"table": s["table"], "id": sid})
            after_log = dict(changed_after)
            after_log.update({"_release": release_id, "_candidate": cid, "_areas_added": sorted(added_areas), "_sources": src_meta})
            plan.ops.append(Op("ChangeLog", "insert", usable("ChangeLog", {
                "id": cuid(release_id, cid, "ChangeLog", kind_log), "entityType": model.upper(), "entityId": rid, "changeType": kind_log,
                "beforeJson": jdump(changed_before) if changed_before else None, "afterJson": jdump(after_log),
                "sourceUrl": (rel.sources.get(cid) or [{}])[0].get("url"), "detectedAt": now_naive})))
            plan.summary["insert:ChangeLog"] += 1

    # proposals: from proposals.jsonl (conflicts found at match time) + conflicts found now; written only if still true and not already there
    for pr in rel.proposals:
        d = props.setdefault(pr["candidate_id"], {"candidate_id": pr["candidate_id"], "entityType": pr["entityType"], "entityId": pr["entityId"], "fields": {}})
        for f, after in pr["afterJson"].items():
            d["fields"].setdefault(f, (pr["beforeJson"].get(f), after))
        d["sourceUrl"] = pr.get("sourceUrl")
    for cid, d in sorted(props.items()):
        entity = d["entityType"].lower()
        row = fetch_row(entity, d["entityId"]) if d.get("entityId") else None
        if row is None:
            continue
        live = {f: (row.get(f), after) for f, (_, after) in d["fields"].items() if _norm(f, row.get(f)) != _norm(f, after)}
        if not live:
            continue
        after_json = {f: a for f, (_, a) in live.items()}
        for h in db.rows('SELECT "afterJson" FROM "ChangeLog" WHERE "entityType" = %s AND "entityId" = %s AND "changeType" = %s', (d["entityType"], d["entityId"], CHANGE_PROPOSED)):
            try:
                old = {k: v for k, v in json.loads(h["afterJson"] or "{}").items() if not k.startswith("_")}
            except ValueError:
                old = {}
            if old == after_json:
                after_json = {}
                break
        if not after_json:
            continue
        src = d.get("sourceUrl") or (rel.sources.get(cid) or [{}])[0].get("url")
        plan.ops.append(Op("ChangeLog", "insert", usable("ChangeLog", {
            "id": cuid(release_id, cid, "ChangeLog", CHANGE_PROPOSED), "entityType": d["entityType"], "entityId": d["entityId"], "changeType": CHANGE_PROPOSED,
            "beforeJson": jdump({f: b for f, (b, _) in live.items()}), "afterJson": jdump({**after_json, "_release": release_id, "_candidate": cid}),
            "sourceUrl": src, "detectedAt": now_naive})))
        plan.summary["insert:ChangeLog(proposal)"] += 1
    return plan


def apply_ops(db: Any, ops: list[Op]) -> Counter:
    done: Counter = Counter()
    for op in ops:
        if op.kind == "insert":
            ks = list(op.values)
            db.execute(f'INSERT INTO "{op.table}" ({", ".join(chr(34) + k + chr(34) for k in ks)}) VALUES ({", ".join(["%s"] * len(ks))})', [op.values[k] for k in ks])
        elif op.kind == "delete":
            ks = list(op.values)
            db.execute(f'DELETE FROM "{op.table}" WHERE ' + " AND ".join(f'"{k}" = %s' for k in ks), [op.values[k] for k in ks])
        elif op.kind == "link":
            ks = list(op.values)
            db.execute(f'INSERT INTO "{op.table}" ({", ".join(chr(34) + k + chr(34) for k in ks)}) VALUES ({", ".join(["%s"] * len(ks))}) ON CONFLICT DO NOTHING',
                       [op.values[k] for k in ks])
        else:
            ks = list(op.values)
            sql = f'UPDATE "{op.table}" SET {", ".join(chr(34) + k + chr(34) + " = %s" for k in ks)} WHERE id = %s'
            params = [op.values[k] for k in ks] + [op.where_id]
            if op.guard_updated_at is not None:
                sql += ' AND "updatedAt" = %s'
                params.append(op.guard_updated_at)
            if db.execute(sql, params) != 1:
                raise LoadAbort(f"{op.table} {op.where_id} was changed by someone else while loading: aborted, nothing is written")
        done[f"{op.kind}:{op.table}"] += 1
    return done


def user_counts(db: Any) -> dict[str, int]:
    out = {}
    for t in USER_TABLES:
        n = db.count(t)
        if n is not None:
            out[t] = n
    return out


def run_load(db: Any, rel: ReleaseData, release_id: str, registry: PlaceholderRegistry, apply: bool, now: Optional[datetime] = None,
             lock_policy: str = "strict") -> dict[str, Any]:
    """Dry-run (apply=False): plan only, no write. Apply: execute, re-plan (must be empty), compare user tables, then the caller commits."""
    now = now or datetime.now(timezone.utc)
    before = user_counts(db)
    plan = plan_load(db, rel, release_id, registry, now, lock_policy)
    result: dict[str, Any] = {"plan": plan.as_json(), "applied": False, "user_tables": before}
    if not apply:
        return result
    done = apply_ops(db, plan.ops)
    again = plan_load(db, rel, release_id, registry, now, lock_policy)
    if again.ops:
        raise LoadAbort(f"load is not idempotent: a second plan still has {len(again.ops)} operations (first: {again.ops[0].table} {again.ops[0].note}); nothing is committed")
    after = user_counts(db)
    if after != before:
        raise LoadAbort(f"user tables changed ({before} -> {after}); nothing is committed")
    result.update({"applied": True, "done": dict(done)})
    return result
