"""Candidates -> match -> plan (tasks 5.1 + 5.2 glued together). No I/O: the caller supplies loaders for existing app rows.
Result per candidate: matched id, action, field changes, and a payload patch (universityId) for the release builder."""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..utils.placeholders import PlaceholderRegistry
from .matchers import (MatchResult, ProfessorMatcher,
                       ProgramMatcher, ScholarshipMatcher, UniversityMatcher)
from .modes import SCRIPT_SOURCES, WRITABLE_DECISIONS, Change, effective_decision, is_human_touched, plan_record, shared_profile_urls

MODEL = {"university": "University", "professor": "Professor",
         "program": "Program", "scholarship": "Scholarship"}


@dataclass
class CandidateResult:
    candidate_id: str
    entity_type: str
    university_name: Optional[str]
    label: str
    # None = could not be planned (ambiguous / university unresolved)
    action: Optional[str]
    matched_id: Optional[str] = None
    via: Optional[str] = None
    changes: list[Change] = field(default_factory=list)
    payload_patch: dict[str, Any] = field(default_factory=dict)
    # the M_* check to store on the candidate
    check: dict[str, Any] = field(default_factory=dict)
    effective: str = ""
    locked: bool = False
    note: Optional[str] = None


def _label(entity: str, p: dict[str, Any]) -> str:
    if entity == "professor":
        return p.get("nameZh") or p.get("name") or "?"
    if entity == "program":
        return f"{p.get('degree')} {p.get('major')}"
    return p.get("name") or p.get("nameZh") or "?"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "blocking": True, "detail": detail}


def plan_candidates(cands: list[dict[str, Any]], *, universities: list[dict[str, Any]],
                    load_rows: Callable[[str, str], list[dict[str, Any]]],
                    load_areas: Callable[[list[str]], dict[str, set[str]]],
                    registry: PlaceholderRegistry, mode: str = "additive", lock_policy: str = "strict") -> list[CandidateResult]:
    """cands: dicts with candidate_id, entity_type, university_name, payload, proposed_status, decision, checks.
    load_rows(entity_type, university_id) -> existing rows of that entity for one university."""
    um = UniversityMatcher(universities)
    matchers: dict[tuple[str, str], Any] = {}
    rows_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    area_cache: dict[str, set[str]] = {}
    out: list[CandidateResult] = []
    shared_urls: dict[str, dict[str, frozenset]] = {"professor": {}}

    def matcher_for(entity: str, uid: str) -> Any:
        key = (entity, uid)
        if key not in matchers:
            rows = load_rows(entity, uid)
            rows_cache[key] = {str(r["id"]): r for r in rows}
            matchers[key] = {"professor": ProfessorMatcher, "program": ProgramMatcher,
                             "scholarship": ScholarshipMatcher}[entity](rows)
            if entity == "professor":
                shared_urls["professor"][uid] = shared_profile_urls(rows)
                area_cache.update({k: v for k, v in load_areas(
                    [str(r["id"]) for r in rows]).items()})
        return matchers[key], rows_cache[key]

    for c in cands:
        entity, payload = c["entity_type"], dict(c["payload"] or {})
        res = CandidateResult(str(c["candidate_id"]), entity, c.get(
            "university_name"), _label(entity, payload), None)
        if entity not in MODEL:
            res.note = f"entity type {entity} is not matched"
            out.append(res)
            continue
        if entity == "university":
            m: MatchResult = um.match(payload)
            existing_by_id = um.by_id
        else:
            uid = um.id_for_name(c.get("university_name") or "")
            if uid is None:
                res.check = _check("M_university_resolved", False,
                                   f"no unique University row for '{c.get('university_name')}'")
                res.effective = effective_decision(c["decision"], [res.check])
                out.append(res)
                continue
            res.payload_patch = {"universityId": uid}
            matcher, existing_by_id = matcher_for(entity, uid)
            m = matcher.match(payload)
        res.via, res.note = m.via, m.note
        if m.status == "ambiguous":
            res.check = _check("M_unique_match", False,
                               f"{m.note}: {', '.join(m.ids)}")
            res.effective = effective_decision(c["decision"], [res.check])
            out.append(res)
            continue
        existing = existing_by_id.get(
            m.entity_id) if m.status == "unique" else None
        res.matched_id = m.entity_id if m.status == "unique" else None
        res.check = _check(
            "M_unique_match", True, f"matched via {m.via}" if existing else "no existing row")
        locked = bool(existing) and entity == "professor" and is_human_touched(
            existing.get("createdAt"), existing.get("updatedAt"), existing.get("lastSyncedAt"))
        if locked and lock_policy == "import-ok" and existing.get("lastSyncedAt") is None and existing.get("dataSource") in SCRIPT_SOURCES:
            # never synced by the pipeline and made by a script: a later updatedAt is not evidence of a person
            locked = False
        areas = area_cache.get(str(existing["id"]), set()) if (
            existing and entity == "professor") else None
        plan = plan_record(MODEL[entity], payload, existing, registry=registry, mode=mode, proposed_status=c.get("proposed_status"),
                           status_writable=c["decision"] in WRITABLE_DECISIONS, locked=locked, existing_areas=areas,
                           shared_urls=shared_urls.get(entity, {}).get(res.payload_patch.get("universityId", ""), frozenset()))
        res.action, res.changes, res.locked = plan.action, plan.changes, locked
        if plan.notes:
            res.note = "; ".join(filter(None, [res.note, *plan.notes]))
        res.effective = effective_decision(c["decision"], [res.check])
        out.append(res)
    return out
