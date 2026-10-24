"""Rollback of one loaded release (task 5.6, doc 03 section 7.3). Uses only what the loader recorded in ChangeLog (afterJson carries
`_release`, `_candidate`, `_areas_added`, `_sources`). Dry-run by default.
 - enriched fields go back to their `beforeJson` value, but ONLY if the field still holds what the release wrote (a later human edit wins);
 - rows the release inserted are deleted only if nobody touched them since and no user data points at them (saved professors, applications);
 - unreviewed proposals of the release are removed; reviewed ones stay; every rolled-back entity gets a PIPELINE_ROLLED_BACK ChangeLog row."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..match.modes import _norm
from .loader import (CHANGE_CREATED, CHANGE_ENRICHED, CHANGE_PROPOSED, CHANGE_ROLLED_BACK, LoadAbort, LoadPlan, Op, apply_ops, cuid, jdump, to_dt, user_counts)

MODEL_OF = {"UNIVERSITY": "University", "PROFESSOR": "Professor", "PROGRAM": "Program", "SCHOLARSHIP": "Scholarship"}
DEPENDENTS = (("SavedProfessor", "professorId"), ("Application", "professorId"))


def _same(field: str, cur: Any, after: Any) -> bool:
    if field.endswith("At"):
        a, b = to_dt(cur), to_dt(after)
        if isinstance(a, datetime) and isinstance(b, datetime):
            return abs((a - b).total_seconds()) < 1
        return a == b
    return _norm(field, cur) == _norm(field, after)


def plan_rollback(db: Any, release_id: str, now: datetime) -> LoadPlan:
    plan = LoadPlan()
    now_naive = to_dt(now)
    logs = db.rows('SELECT * FROM "ChangeLog" WHERE "afterJson" LIKE %s ORDER BY "detectedAt"', (f'%"_release": "{release_id}"%',))
    if not logs:
        plan.skipped.append("no ChangeLog rows for this release (was it loaded?)")
    for lg in logs:
        after = json.loads(lg["afterJson"] or "{}")
        before = json.loads(lg["beforeJson"] or "{}")
        meta_sources = after.get("_sources") or []
        fields = {k: v for k, v in after.items() if not k.startswith("_")}
        model = MODEL_OF.get(lg["entityType"])
        if model is None or after.get("_release") != release_id:
            continue
        eid, ctype = lg["entityId"], lg["changeType"]
        if ctype == CHANGE_PROPOSED:
            if lg.get("reviewedAt") is None:
                plan.ops.append(Op("ChangeLog", "delete", {"id": lg["id"]}, note="unreviewed proposal"))
                plan.summary["delete:ChangeLog(proposal)"] += 1
            else:
                plan.skipped.append(f"proposal {lg['id']} was reviewed: kept")
            continue
        if ctype not in (CHANGE_CREATED, CHANGE_ENRICHED):
            continue
        rows = db.rows(f'SELECT * FROM "{model}" WHERE id = %s', (eid,))
        if not rows:
            plan.skipped.append(f"{model} {eid}: already gone")
            continue
        row = rows[0]
        link_ops: list[Op] = []

        def add_delete(table: str, where: dict[str, Any]) -> None:
            if db.rows(f'SELECT 1 AS x FROM "{table}" WHERE ' + " AND ".join(f'"{k}" = %s' for k in where), list(where.values())):
                link_ops.append(Op(table, "delete", where))

        for s in meta_sources:
            add_delete(s["table"], {"id": s["id"]})
        if model == "Professor":
            if ctype == CHANGE_CREATED:
                add_delete("ProfessorResearchArea", {"professorId": eid})
            else:
                for name in after.get("_areas_added") or []:
                    a = db.rows('SELECT id FROM "ResearchArea" WHERE name = %s', (name,))
                    if a:
                        add_delete("ProfessorResearchArea", {"professorId": eid, "researchAreaId": a[0]["id"]})
        if ctype == CHANGE_CREATED:
            loaded = to_dt(lg["detectedAt"])
            upd = to_dt(row.get("updatedAt"))
            if isinstance(loaded, datetime) and isinstance(upd, datetime) and (upd - loaded) > timedelta(seconds=60):
                plan.skipped.append(f"{model} {eid}: edited after the load, not deleted")
                continue
            blockers = [t for t, c in DEPENDENTS if model == "Professor" and c in db.columns(t) and db.rows(f'SELECT 1 AS x FROM "{t}" WHERE "{c}" = %s', (eid,))]
            if blockers:
                plan.skipped.append(f"{model} {eid}: user data refers to it ({', '.join(blockers)}), not deleted")
                continue
            plan.ops.extend(link_ops)
            plan.ops.append(Op(model, "delete", {"id": eid}, note=f"delete inserted {model}"))
            plan.summary[f"delete:{model}"] += 1
        else:
            restore, kept = {}, []
            for f, b in before.items():
                if _same(f, row.get(f), fields.get(f)):
                    restore[f] = to_dt(b) if f.endswith("At") else b
                else:
                    kept.append(f)
            if kept:
                plan.skipped.append(f"{model} {eid}: fields changed since the load, left as they are: {', '.join(sorted(kept))}")
            plan.ops.extend(link_ops)
            if restore:
                restore["updatedAt"] = now_naive
                if model == "Professor":
                    restore["lastSyncedAt"] = now_naive
                plan.ops.append(Op(model, "update", {k: v for k, v in restore.items() if k in db.columns(model)}, where_id=eid, note="restore fields"))
                plan.summary[f"restore:{model}"] += 1
            if not restore and not link_ops:
                continue                                   # nothing left to undo (already rolled back)
        plan.ops.append(Op("ChangeLog", "insert", {
            "id": cuid(release_id, lg["id"], "rollback"), "entityType": lg["entityType"], "entityId": eid, "changeType": CHANGE_ROLLED_BACK,
            "beforeJson": None, "afterJson": jdump({"_release": release_id, "_rolled_back": ctype}), "sourceUrl": None, "detectedAt": now_naive}))
    return plan


def run_rollback(db: Any, release_id: str, apply: bool, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    before = user_counts(db)
    plan = plan_rollback(db, release_id, now)
    out: dict[str, Any] = {"plan": plan.as_json(), "applied": False}
    if not apply:
        return out
    done = apply_ops(db, plan.ops)
    if user_counts(db) != before:
        raise LoadAbort("user tables changed during rollback; nothing is committed")
    out.update({"applied": True, "done": dict(done)})
    return out
