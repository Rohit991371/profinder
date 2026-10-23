"""Programs and scholarships from official admissions/scholarship pages (task 4.6).
Policy (doc 02 §4.3/4.4, decisions D7/D8): a Program candidate only says "this degree + major exists on this official page" and may be
VERIFIED on that basis; a Scholarship candidate is always needs_review. Deadlines, opening dates, tuition and fees are NEVER put in
the payload: the grounded sentence stays in `evidence` (field `requires_human:<name>`) for the review CSV (Iteration 5)."""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..fetch.cache import PageCache
from ..fetch.clean import html_to_markdown, truncate_for_llm
from ..paths import OUTPUT_DIR
from ..schemas.candidate import Candidate, CheckResult, EvidenceItem
from ..schemas.entities import ProgramRecord, ScholarshipRecord
from ..schemas.enums import Decision, EntityType, Extractor, ProposedStatus
from ..schemas.status import status_pair, status_timestamps
from ..utils.keys import program_key, scholarship_key
from ..utils.placeholders import PlaceholderRegistry
from ..utils.text import normalise_key_text
from ..utils.urls import canonical_url, host_in_domains
from ..validate.decide import decide_checks
from ..validate.grounding import norm_text, text_in
from ..validate.professor_logic import Check, SourcePage
from .css import visible_text
from .llm import ProgramsOut, programs_from_markdown
from .run import PAGE_CACHE_DIR, _db_connect, _read_cached, load_domain_info

OUT_DIR = OUTPUT_DIR / "extract"
HELD_BACK = (("deadline_text", "deadline"), ("tuition_text", "tuition"), ("application_fee_text", "applicationFee"))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ----------------------------------------------------------------------------------------------- mapping (each needs words ON the page)
def map_degree(text: Optional[str]) -> Optional[str]:
    t = (text or "").casefold().replace(" ", "")
    if any(k in t for k in ("phd", "ph.d", "doctor", "博士")):
        return "PhD"
    if any(k in t for k in ("master", "硕士", "postgraduate")):
        return "Master"
    return None


def map_language(text: Optional[str]) -> Optional[str]:
    t = (text or "").casefold()
    en = any(k in t for k in ("english", "英语", "英文"))
    zh = any(k in t for k in ("chinese", "中文", "汉语", "mandarin"))
    return "English and Chinese" if en and zh else "English" if en else "Chinese" if zh else None


def map_scholarship_type(text: Optional[str]) -> Optional[str]:
    t = (text or "").casefold()
    if "csc" in t or "中国政府奖学金" in t or "chinese government" in t:
        return "CSC"
    if "presidential" in t or "校长" in t:
        return "Presidential"
    if "university" in t or "校级" in t or "学校奖学金" in t:
        return "University"
    return None


def map_coverage(text: Optional[str]) -> Optional[str]:
    t = (text or "").casefold()
    if any(k in t for k in ("全额", "full", "fully")):
        return "Full"
    if any(k in t for k in ("部分", "partial")):
        return "Partial"
    return None


def _grounded(value: Optional[str], page: SourcePage) -> Optional[str]:
    v = (value or "").strip()
    return v if v and text_in(v, page.text_norm) else None


def _page_checks(page: SourcePage) -> Check:
    return Check("B1_official_page_200", page.status == 200 and page.host_ok, True, f"HTTP {page.status}, host {'allowed' if page.host_ok else 'NOT allowed'}")


# ----------------------------------------------------------------------------------------------- candidates
def build_candidates(out: ProgramsOut, page: SourcePage, university: str, kind: str, registry: PlaceholderRegistry, now: datetime,
                     model: Optional[str]) -> tuple[list[Candidate], dict[str, int]]:
    cands: list[Candidate] = []
    drop = {"no_degree": 0, "no_major": 0, "ungrounded": 0, "duplicate": 0}
    seen: set[str] = set()
    uref = normalise_key_text(university)
    if kind == "admissions":
        for p in out.programs:
            degree = map_degree(p.degree)
            major = (p.major or "").strip()
            if degree is None:
                drop["no_degree"] += 1
                continue
            quote = _grounded(p.evidence_quote, page)
            if not major:
                drop["no_major"] += 1
                continue
            if not text_in(major, page.text_norm) or quote is None:
                drop["ungrounded"] += 1
                continue
            key = program_key(uref, degree, normalise_key_text(major))
            if key in seen:
                drop["duplicate"] += 1
                continue
            seen.add(key)
            checks = [_page_checks(page), Check("B2_major_in_page_text", True, True, major),
                      Check("B3_degree_stated_in_quote", map_degree(quote) == degree or map_degree(p.official_name) == degree, True, degree)]
            ev: list[tuple[str, str]] = [("program_quote", quote)]
            held = []
            for attr, label in HELD_BACK:
                g = _grounded(getattr(p, attr), page)
                if g:
                    ev.append((f"requires_human:{label}", g))
                    held.append(label)
            if held:
                checks.append(Check("H_human_approval_pending", False, False, "held back until a human approves: " + ", ".join(held)))
            lang = _grounded(p.teaching_language, page)
            official = _grounded(p.official_name, page)
            decision, proposed, reason = decide_checks(checks)
            pair = status_pair(EntityType.program, proposed)
            stamps = status_timestamps(EntityType.program, proposed, page.fetched_at, now)
            rec = ProgramRecord(degree=degree, major=major, officialName=official, teachingLang=map_language(lang), programUrl=page.url,
                                sourceUrl=page.url, verificationStatus=pair.verification_status, confidence=0.8, **stamps)
            placeholders = registry.placeholder_fields("Program", rec.model_dump(mode="json"))
            if placeholders:
                checks.append(Check("B5_no_placeholders", False, True, ",".join(placeholders)))
                decision, proposed, reason = decide_checks(checks)
            cands.append(_candidate(EntityType.program, key, university, rec, page, ev, checks, decision, proposed, reason, model))
    else:
        for sc in out.scholarships:
            name = _grounded(sc.name, page)
            quote = _grounded(sc.evidence_quote, page)
            if name is None or quote is None:
                drop["ungrounded"] += 1
                continue
            key = scholarship_key(uref, normalise_key_text(name))
            if key in seen:
                drop["duplicate"] += 1
                continue
            seen.add(key)
            checks = [_page_checks(page), Check("B2_name_in_page_text", True, True, name)]
            ev = [("scholarship_quote", quote)]
            held = []
            g = _grounded(sc.deadline_text, page)
            if g:
                ev.append(("requires_human:deadline", g))
                held.append("deadline")
            if held:
                checks.append(Check("H_human_approval_pending", False, False, "held back until a human approves: " + ", ".join(held)))
            decision, proposed, reason = decide_checks(checks)
            if decision != Decision.rejected:                 # doc 02 §4.4: scholarships are never auto-verified
                decision, proposed = Decision.needs_review, ProposedStatus.needs_review
            pair = status_pair(EntityType.scholarship, proposed)
            stamps = status_timestamps(EntityType.scholarship, proposed, page.fetched_at, now)
            rec = ScholarshipRecord(name=name, type=map_scholarship_type(_grounded(sc.type_hint, page)), officialUrl=page.url,
                                    coverage=map_coverage(_grounded(sc.coverage_text, page)), dataStatus=pair.data_status,
                                    verificationStatus=pair.verification_status, confidence=0.7, **stamps)
            cands.append(_candidate(EntityType.scholarship, key, university, rec, page, ev, checks, decision, proposed, reason, model))
    return cands, drop


def _candidate(entity: EntityType, key: str, university: str, rec: Any, page: SourcePage, ev: list[tuple[str, str]], checks: list[Check],
               decision: Decision, proposed: ProposedStatus, reason: Optional[str], model: Optional[str]) -> Candidate:
    return Candidate(
        entity_type=entity, natural_key=key, university_name=university, payload=rec, source_url=page.url, page_id=page.page_id,
        evidence=[EvidenceItem(field=f, quote=q[:500]) for f, q in ev if q], extractor=Extractor.llm, model=model,
        prompt_version="programs-v1", confidence=rec.confidence,
        checks=[CheckResult(name=c.name, passed=c.passed, blocking=c.blocking, detail=c.detail) for c in checks],
        proposed_status=proposed, decision=decision, reject_reason=reason, fetched_at=page.fetched_at)


# ----------------------------------------------------------------------------------------------- run
def _targets(conn: Any, university: str) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT kind, url FROM ingest.target WHERE university_name = %s AND kind IN ('admissions','scholarship') "
                        "AND status IN ('pending','ok') ORDER BY kind, url", (university,)).fetchall()
    return [(k, u) for k, u in rows]


def cmd_programs(args: argparse.Namespace) -> int:
    from ..db.ingest_store import finish_run, insert_candidate, start_run
    from ..schemas.enums import RunKind, RunStatus
    from .llm import build_client

    if args.html and not (args.url and args.kind and len(args.only or []) == 1):
        raise SystemExit("--html needs exactly one --only plus --url and --kind (admissions|scholarship)")
    client = build_client()
    registry = PlaceholderRegistry.from_yaml()
    cache = PageCache(PAGE_CACHE_DIR)
    conn = None if args.html else _db_connect()          # the database is read for the target URLs; written only without --dry-run/--no-db
    try:
        for university in args.only or []:
            domains, _e = load_domain_info(university)
            if args.html:
                targets = [(args.kind, args.url)]
            else:
                targets = _targets(conn, university)
            now = datetime.now(timezone.utc)
            cands: list[Candidate] = []
            notes: list[str] = []
            for kind, url in targets:
                if args.html:
                    html = Path(args.html).read_text(encoding="utf-8")
                    page = SourcePage(url, 200, now, visible_text(html), None, host_in_domains(url, domains))
                else:
                    pc = _read_cached(cache, url)
                    if pc is None:
                        notes.append(f"{kind}: {url} not in the page cache (run discover/fetch first)")
                        continue
                    html = pc.html
                    page = SourcePage(url, pc.status, pc.fetched_at, visible_text(html), None, host_in_domains(pc.final_url or url, domains))
                md = html_to_markdown(html, url).markdown
                out, failed, used = programs_from_markdown(client, truncate_for_llm(md, 3500 * args.max_chunks), url, max_chunks=args.max_chunks)
                got, drop = build_candidates(out, page, university, kind, registry, now, client.model)
                cands += got
                notes.append(f"{kind}: {url} -> {len(got)} candidates ({len(out.programs)} programs / {len(out.scholarships)} scholarships proposed by the model, "
                             f"dropped {drop}, failed chunks {failed}/{used})")
            slug = slugify(university)
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            with (OUT_DIR / f"programs_{slug}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                for c in cands:
                    fh.write(c.model_dump_json() + "\n")
            print(f"[{university}] {len(cands)} candidates: " + ", ".join(f"{k} {sum(1 for c in cands if c.entity_type.value == k)}" for k in ("program", "scholarship")))
            for n in notes:
                print("  ", n)
            if conn is not None and not (args.dry_run or args.no_db) and cands:
                run_id = start_run(conn, RunKind.extract, {"university": university, "stage": "programs", "llm": True})
                for c in cands:
                    insert_candidate(conn, run_id, c)
                finish_run(conn, run_id, RunStatus.ok, {"candidates": len(cands), "notes": notes})
                conn.commit()
                print(f"   ingest.candidate: {len(cands)} rows written (run {run_id[:8]})")
        t = client.totals
        print(f"LLM: {int(t['calls'])} calls, {int(t['prompt'])} prompt + {int(t['completion'])} completion tokens, ~USD {t['usd']:.4f}")
    finally:
        if conn is not None:
            conn.close()
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("programs", help="programs + scholarships from the cached admissions/scholarship pages (uses Groq)")
    p.add_argument("--only", action="append", required=True, help="university name (repeatable)")
    p.add_argument("--max-chunks", type=int, default=4, help="page chunks sent to Groq per page (default 4, about 3,500 characters each)")
    p.add_argument("--no-db", action="store_true", help="same as --dry-run: the target URLs are still read from the database, nothing is written")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--html", help="test on a saved page instead of the cache/database (needs --url and --kind)")
    p.add_argument("--url")
    p.add_argument("--kind", choices=["admissions", "scholarship"])
    p.set_defaults(fn=cmd_programs)
