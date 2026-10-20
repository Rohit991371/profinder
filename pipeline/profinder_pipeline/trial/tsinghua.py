"""Trial A (Tsinghua CS faculty list): the Iteration 0 spike, promoted onto the real schemas.

parse   list page -> Candidate JSONL (strict models, checks, decisions)      output/trial/candidates.jsonl
compare candidates vs ground truth -> report.md                               output/trial/report.md
land    candidates -> ingest.run / target / page / candidate (local DB only)
explain-missing  show where the ground-truth people that we missed sit in the saved list page
drop-spike       drop the Iteration 0 schema `spike` (local DB only)
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from ..extract.tsinghua_list import INFO_RE, RawEntry, parse_list_html, visible_text
from ..fetch.basic import FetchResult, fetch_text
from ..paths import INPUTS_DIR, OUTPUT_DIR
from ..schemas.candidate import Candidate, CheckResult, EvidenceItem
from ..schemas.entities import ProfessorRecord
from ..schemas.enums import Decision, EntityType, Extractor, ProposedStatus
from ..schemas.status import status_pair, status_timestamps
from ..utils.emails import email_domain_ok, is_valid_email
from ..utils.keys import professor_key
from ..utils.names import romanise
from ..utils.placeholders import PlaceholderRegistry
from ..utils.positions import normalise_position
from ..utils.text import clean_name, normalise_key_text
from ..utils.urls import canonical_url

OUT_DIR = OUTPUT_DIR / "trial"
LIST_URL = "https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm"
UNIVERSITY = "Tsinghua University"
SCHOOL_EN = "Department of Computer Science and Technology"   # Iteration 2 moves this to config/universities.yml
ALLOWED_EMAIL_DOMAINS = ["tsinghua.edu.cn"]
PROFILE_FILES = {
    "tsinghua_prof1.html": "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm",
    "tsinghua_prof2.html": "https://www.cs.tsinghua.edu.cn/info/1121/3551.htm",
}
# Numbers from the Iteration 0 report (offline run on the saved list page). `compare` shows whether the new code reproduces them.
SPIKE_BASELINE = {"candidates": 135, "found": 134, "missing": 2, "extra": 1, "emails_equal": 133,
                  "auto_accept": 115, "needs_review": 8, "rejected": 12}
_STATUS_FOR = {Decision.auto_accept: ProposedStatus.verified, Decision.needs_review: ProposedStatus.needs_review,
               Decision.rejected: ProposedStatus.unverified}


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def build_candidate(raw: RawEntry, fetched_at: str, http_status: Optional[int], page_source: str, page_text_norm: str,
                    allowed_domains: list[str], registry: PlaceholderRegistry, require_live: bool = False,
                    now: Optional[datetime] = None) -> Candidate:
    now = now or datetime.now(timezone.utc)
    fetched = datetime.fromisoformat(fetched_at)
    name_zh, note = clean_name(raw.name_raw)
    rom = romanise(name_zh)
    position_en, role_class = normalise_position(raw.position_zh)

    valid_emails = [e for e in raw.emails if is_valid_email(e)]
    seen = valid_emails[0] if valid_emails else None
    domain_ok = email_domain_ok(seen, allowed_domains) if seen else None
    email = seen if domain_ok else None
    personal = bool(INFO_RE.search(raw.profile_url)) and canonical_url(raw.profile_url) != canonical_url(LIST_URL)

    b1 = http_status == 200 or (page_source == "saved_file" and not require_live)
    checks = [
        CheckResult(name="B1_official_page_200", passed=b1,
                    detail=f"live HTTP {http_status}" if http_status == 200 else
                    ("saved copy of the live page (inputs/); not re-fetched" if page_source == "saved_file" else f"HTTP {http_status}")),
        CheckResult(name="B2_name_in_page_text", passed=_norm_ws(name_zh) in page_text_norm, detail=name_zh),
        CheckResult(name="B3_faculty_role", passed=role_class == "faculty", detail=raw.position_zh),
        CheckResult(name="B4_school_from_page", passed=True, detail="department of the source site (config)"),
        CheckResult(name="F_email_domain", passed=seen is None or bool(domain_ok), blocking=False,
                    detail=None if seen is None else seen.rsplit("@", 1)[-1]),
        CheckResult(name="S_concurrent_post", passed=note is None, blocking=False, detail=note),
        CheckResult(name="S_single_email", passed=len(valid_emails) <= 1, blocking=False, detail=str(len(valid_emails))),
    ]

    if role_class == "non_faculty":
        decision, reason = Decision.rejected, f"non_faculty_role:{raw.position_zh}"
    elif role_class == "unknown":
        decision, reason = Decision.needs_review, None
    elif note:
        decision, reason = Decision.needs_review, None
    elif any(c.blocking and not c.passed for c in checks):
        decision, reason = Decision.needs_review, None
    else:
        decision, reason = Decision.auto_accept, None
    proposed = _STATUS_FOR[decision]

    pair = status_pair(EntityType.professor, proposed)
    stamps = status_timestamps(EntityType.professor, proposed, fetched, now)
    record = ProfessorRecord(
        name=rom[0] if rom else name_zh, firstName=rom[1] if rom else None, lastName=rom[2] if rom else None,
        nameZh=name_zh, position=position_en, school=SCHOOL_EN, email=email,
        profileUrl=raw.profile_url if personal else None, profileIsPersonal=personal,
        dataSource="official_faculty_page", dataStatus=pair.data_status, verificationStatus=pair.verification_status, **stamps)
    # B5 runs on what we would store.
    placeholders = registry.placeholder_fields("Professor", record.model_dump(mode="json"))
    checks.append(CheckResult(name="B5_no_placeholders", passed=not placeholders, detail=",".join(placeholders) or None))
    if placeholders and decision == Decision.auto_accept:
        decision, proposed = Decision.needs_review, ProposedStatus.needs_review
        pair = status_pair(EntityType.professor, proposed)
        record = record.model_copy(update={"dataStatus": pair.data_status, "verificationStatus": pair.verification_status,
                                           "verifiedAt": None, "lastVerifiedAt": None})

    evidence = [EvidenceItem(field="position_zh", quote=raw.position_zh)] if raw.position_zh else []
    for fld, val in (("email_seen_on_page", seen), ("institute_zh", raw.institute_zh), ("rank_group_zh", raw.rank_group_zh), ("name_note_zh", note)):
        if val:
            evidence.append(EvidenceItem(field=fld, quote=val))
    return Candidate(
        entity_type=EntityType.professor,
        natural_key=professor_key(normalise_key_text(UNIVERSITY), raw.profile_url, personal, name_zh, rom[0] if rom else None, None),
        university_name=UNIVERSITY, payload=record, source_url=LIST_URL, evidence=evidence, extractor=Extractor.css,
        checks=checks, proposed_status=proposed, decision=decision, reject_reason=reason, fetched_at=fetched)


def evidence_value(c: Candidate, field: str) -> Optional[str]:
    return next((e.quote for e in c.evidence if e.field == field), None)


def read_candidates() -> list[Candidate]:
    path = OUT_DIR / "candidates.jsonl"
    return [Candidate.model_validate_json(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ----------------------------------------------------------------------------------------------- parse
def run_parse(offline: bool, extra_domains: list[str], require_live: bool = False) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    allowed = ALLOWED_EMAIL_DOMAINS + extra_domains
    res = fetch_text(LIST_URL, INPUTS_DIR / "tsinghua_list.html", offline=offline)
    parsed = parse_list_html(res.text, LIST_URL)
    page_text = visible_text(res.text)
    registry = PlaceholderRegistry.from_yaml()
    cands = [build_candidate(e, res.fetched_at, res.http_status, res.source, _norm_ws(page_text), allowed, registry, require_live)
             for e in parsed.entries]
    with (OUT_DIR / "candidates.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for c in cands:
            fh.write(c.model_dump_json() + "\n")
    (OUT_DIR / "list_page.txt").write_text(page_text, encoding="utf-8")

    checks = _check_profiles(cands, offline)
    (OUT_DIR / "profile_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    summary: dict[str, Any] = {
        "list_source": res.source, "http_status": res.http_status, "robots": res.robots_note, "encoding": res.encoding,
        "fetched_at": res.fetched_at, "parser_mode": parsed.mode, "entries_raw": parsed.raw_entry_count,
        "duplicate_urls_dropped": parsed.duplicate_urls, "candidates": len(cands),
        "by_decision": dict(Counter(c.decision.value for c in cands)),
        "by_position_zh": dict(Counter(evidence_value(c, "position_zh") for c in cands)),
        "with_email_seen": sum(1 for c in cands if evidence_value(c, "email_seen_on_page")),
        "email_kept": sum(1 for c in cands if c.payload.email), "allowed_email_domains": allowed,
        "require_live": require_live,
    }
    (OUT_DIR / "parse_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"list: {res.source}, parser mode '{parsed.mode}', {len(cands)} candidates ({parsed.duplicate_urls} duplicate URLs dropped)")
    print("decisions:", summary["by_decision"])
    print(f"emails: {summary['with_email_seen']} seen on page, {summary['email_kept']} kept after domain rule")
    for ch in checks:
        print("profile check:", ch)
    for c in cands[:3]:
        p = c.payload
        print(" sample:", p.nameZh, "|", p.name, "|", p.position, "|", p.email, "|", p.profileUrl)
    print(f"written: {OUT_DIR / 'candidates.jsonl'}")
    return 0


def _check_profiles(cands: list[Candidate], offline: bool) -> list[dict[str, Any]]:
    by_url = {c.payload.profileUrl: c for c in cands if c.payload.profileUrl}
    out: list[dict[str, Any]] = []
    for fname, url in PROFILE_FILES.items():
        path = INPUTS_DIR / fname
        if not path.exists():
            out.append({"file": fname, "status": "missing"})
            continue
        res = fetch_text(url, path, offline=offline)
        text = visible_text(res.text)
        c = by_url.get(canonical_url(url))
        seen = evidence_value(c, "email_seen_on_page") if c else None
        out.append({"file": fname, "url": url, "source": res.source, "text_chars": len(text),
                    "candidate_found_in_list": c is not None, "name_zh": c.payload.nameZh if c else None,
                    "name_found_in_page_text": bool(c and c.payload.nameZh and c.payload.nameZh in _norm_ws(text)),
                    "email_found_in_page_text": bool(seen and seen in text.lower())})
    return out


# --------------------------------------------------------------------------------------------- compare
def _pct(a: int, b: int) -> str:
    return f"{(100 * a / b):.1f}%" if b else "n/a"


def run_compare() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gt = json.loads((INPUTS_DIR / "faculty-test-5.json").read_text(encoding="utf-8"))["records"][UNIVERSITY]
    cands = read_candidates()
    gt_by_url = {canonical_url(r["profileUrl"]): r for r in gt if r.get("profileUrl")}
    cand_by_url = {canonical_url(c.payload.profileUrl): c for c in cands if c.payload.profileUrl}
    found = sorted(set(gt_by_url) & set(cand_by_url))
    missing = sorted(set(gt_by_url) - set(cand_by_url))
    extra = sorted(set(cand_by_url) - set(gt_by_url))
    found_not_rejected = [u for u in found if cand_by_url[u].decision != Decision.rejected]
    # The ground truth also lists engineers/technicians, so "recall" is also reported on its faculty-only subset.
    gt_faculty = [u for u, r in gt_by_url.items() if normalise_position(r.get("position"))[1] == "faculty"]
    found_faculty = [u for u in gt_faculty if u in cand_by_url and cand_by_url[u].decision != Decision.rejected]

    gt_with_email = [u for u in found if gt_by_url[u].get("email")]
    seen = {u: (evidence_value(cand_by_url[u], "email_seen_on_page") or "") for u in found}
    raw_match = [u for u in gt_with_email if seen[u] == gt_by_url[u]["email"].lower()]
    kept_match = [u for u in gt_with_email if (cand_by_url[u].payload.email or "") == gt_by_url[u]["email"].lower()]
    mismatches = [(u, gt_by_url[u]["email"], seen[u]) for u in gt_with_email if u not in raw_match]
    dropped = Counter(s.rsplit("@", 1)[-1] for c in cands if (s := evidence_value(c, "email_seen_on_page")) and not c.payload.email)
    invalid = [c.payload.nameZh for c in cands if c.payload.email and not is_valid_email(c.payload.email)]
    shared = [c.payload.nameZh for c in cands if c.payload.profileUrl and
              (not INFO_RE.search(c.payload.profileUrl) or canonical_url(c.payload.profileUrl) == canonical_url(LIST_URL))]
    verified_failing = [c.payload.nameZh for c in cands if c.proposed_status == ProposedStatus.verified
                        and any(k.blocking and not k.passed for k in c.checks)]
    by_dec = Counter(c.decision.value for c in cands)

    groq = _read_json(OUT_DIR / "groq_result.json")
    land = _read_json(OUT_DIR / "land_result.json")
    checks = _read_json(OUT_DIR / "profile_checks.json") or []
    now_vals = {"candidates": len(cands), "found": len(found), "missing": len(missing), "extra": len(extra),
                "emails_equal": len(raw_match), "auto_accept": by_dec["auto_accept"],
                "needs_review": by_dec["needs_review"], "rejected": by_dec["rejected"]}

    L = ["# Trial A report - Tsinghua CS faculty (Iteration 1 CLI)", "",
         f"Ground truth: **{len(gt)}** records ({len(gt_by_url)} unique profile URLs). Parsed from the list page: **{len(cands)}** entries.", "",
         "## Coverage vs ground truth", "",
         f"- Found by profile URL (before role filter): **{len(found)} / {len(gt_by_url)}** ({_pct(len(found), len(gt_by_url))})",
         f"- Found and not rejected by the role rule: {len(found_not_rejected)} / {len(gt_by_url)} ({_pct(len(found_not_rejected), len(gt_by_url))})",
         f"- **Faculty-only recall** (ground-truth people whose position is a faculty role): **{len(found_faculty)} / {len(gt_faculty)}** "
         f"({_pct(len(found_faculty), len(gt_faculty))})",
         f"- In ground truth but missing from our parse: {len(missing)}",
         f"- Parsed but not in ground truth: {len(extra)}", ""]
    if missing:
        L += ["Missing (first 15):"] + [f"- {u} ({gt_by_url[u]['name']}, {gt_by_url[u].get('position')})" for u in missing[:15]] + [""]
    if extra:
        L += ["Extra (first 15):"] + [f"- {u} ({cand_by_url[u].payload.nameZh}, {evidence_value(cand_by_url[u], 'position_zh')}, {cand_by_url[u].decision.value})"
                                      for u in extra[:15]] + [""]
    L += ["## Decisions", "", "| decision | count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(by_dec.items())]
    failed = Counter(k.name for c in cands for k in c.checks if not k.passed)
    L += ["", "Failed checks:", ""] + [f"- {k}: {v}" for k, v in failed.most_common()]
    L += ["", "## Emails", "",
          f"- Ground-truth people with an email that we found: {len(gt_with_email)}",
          f"- Page email equals ground truth: **{len(raw_match)}** ({_pct(len(raw_match), len(gt_with_email))})",
          f"- Kept after the university-domain rule and equal: {len(kept_match)} ({_pct(len(kept_match), len(gt_with_email))})",
          f"- Emails dropped by the domain rule: {sum(dropped.values())} - domains: {dict(dropped)}",
          f"- Invalid emails in output: **{len(invalid)}**",
          f"- Shared-directory URLs stored as profileUrl: **{len(shared)}**",
          f"- Records `verified` with a failed blocking check: **{len(verified_failing)}**", ""]
    if mismatches:
        L += ["Email mismatches (first 10):"] + [f"- {u}: ground truth {a} vs page {b}" for u, a, b in mismatches[:10]] + [""]
    L += ["## Profile pages checked", ""] + [f"- {c}" for c in checks] + [""]
    L += ["## Groq smoke test", "", f"```json\n{json.dumps(groq, ensure_ascii=False, indent=2) if groq else 'not run yet'}\n```", "",
          "## Database (ingest.candidate)", "", f"```json\n{json.dumps(land, indent=2) if land else 'not run yet'}\n```", "",
          "## Spike reproduction (Iteration 1, task 1.7)", "", "| metric | spike | now | same |", "|---|---|---|---|"]
    for k, base in SPIKE_BASELINE.items():
        L.append(f"| {k} | {base} | {now_vals[k]} | {'yes' if base == now_vals[k] else '**NO**'} |")
    (OUT_DIR / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"found {len(found)}/{len(gt_by_url)} ({_pct(len(found), len(gt_by_url))}); faculty-only recall {len(found_faculty)}/{len(gt_faculty)}; "
          f"missing {len(missing)}; extra {len(extra)}")
    print(f"emails equal: {len(raw_match)}/{len(gt_with_email)}; dropped by domain rule: {sum(dropped.values())} {dict(dropped)}")
    print(f"invalid emails: {len(invalid)}; shared-directory URLs: {len(shared)}; verified-with-failed-blocking: {len(verified_failing)}")
    same = all(SPIKE_BASELINE[k] == now_vals[k] for k in SPIKE_BASELINE)
    for k, base in SPIKE_BASELINE.items():
        print(f"{'SAME' if base == now_vals[k] else 'DIFF'} {k}: spike {base} / now {now_vals[k]}")
    print("report:", OUT_DIR / "report.md")
    return 0 if same else 2


def _read_json(path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --------------------------------------------------------------------------------------- explain-missing
def run_explain_missing() -> int:
    gt = json.loads((INPUTS_DIR / "faculty-test-5.json").read_text(encoding="utf-8"))["records"][UNIVERSITY]
    cand_urls = {canonical_url(c.payload.profileUrl) for c in read_candidates() if c.payload.profileUrl}
    miss = [r for r in gt if r.get("profileUrl") and canonical_url(r["profileUrl"]) not in cand_urls]
    html_path = INPUTS_DIR / "tsinghua_list.html"
    html = html_path.read_bytes().decode("utf-8-sig", errors="replace") if html_path.exists() else ""
    print(f"{len(miss)} ground-truth people are not in our parse")
    for r in miss:
        path = "/" + r["profileUrl"].split("/", 3)[3]
        print(f"\n--- {r['name']} ({r.get('position')}) {path}")
        hits = [m.start() for m in re.finditer(re.escape(path), html)] or [m.start() for m in re.finditer(re.escape(r["name"]), html)]
        if not hits:
            print("not present in the saved list page (the list may have changed since the ground truth was made)")
            continue
        for pos in hits[:2]:
            print(re.sub(r"\s+", " ", html[max(0, pos - 350): pos + 350]))
            print("   ...")
    return 0


# -------------------------------------------------------------------------------------------- land / drop
def run_land() -> int:
    from ..db.connection import connect
    from ..db.hostguard import host_label, require_local
    from ..db.ingest_store import finish_run, insert_candidate, insert_page, start_run, upsert_target
    from ..schemas.enums import DiscoveredBy, RenderMode, RunKind, RunStatus, TargetKind
    from ..settings import get_settings

    url = get_settings().pipeline_database_url
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    require_local(url, "trial land")
    summary = _read_json(OUT_DIR / "parse_summary.json")
    cands = read_candidates()
    if not summary or not cands:
        raise SystemExit("run `trial-tsinghua parse` first")
    page_text = (OUT_DIR / "list_page.txt").read_text(encoding="utf-8")
    print("database host:", host_label(url))
    with connect(url) as conn:
        run_id = start_run(conn, RunKind.extract, {"trial": "tsinghua_cs", "list_source": summary["list_source"]})
        target_id = upsert_target(conn, UNIVERSITY, TargetKind.faculty_list, LIST_URL, DiscoveredBy.seed)
        page_id = insert_page(conn, target_id, LIST_URL, summary.get("http_status"),
                              RenderMode.static, summary["encoding"], page_text, datetime.fromisoformat(summary["fetched_at"]))
        for c in cands:
            insert_candidate(conn, run_id, c.model_copy(update={"page_id": page_id}))
        finish_run(conn, run_id, RunStatus.ok, {"candidates": len(cands), "by_decision": summary["by_decision"]})
        conn.commit()
        rows = conn.execute("SELECT count(*) FROM ingest.candidate WHERE run_id = %s", (run_id,)).fetchone()[0]
        by_dec = dict(conn.execute("SELECT decision, count(*) FROM ingest.candidate WHERE run_id = %s GROUP BY 1", (run_id,)).fetchall())
        bad = conn.execute("SELECT count(*) FROM ingest.candidate WHERE run_id = %s AND proposed_status = 'verified' AND decision <> 'auto_accept'",
                           (run_id,)).fetchone()[0]
    result = {"jsonl_lines": len(cands), "db_rows": rows, "by_decision": by_dec, "run_id": run_id}
    (OUT_DIR / "land_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"JSONL lines: {len(cands)} | DB rows (this run): {rows} | match: {len(cands) == rows}")
    print("by decision:", by_dec, "| verified-but-not-auto-accepted:", bad)
    return 0 if len(cands) == rows else 1


def run_drop_spike() -> int:
    from ..db.connection import connect
    from ..db.hostguard import require_local
    from ..settings import get_settings

    url = get_settings().pipeline_database_url
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    print("database host:", require_local(url, "drop-spike"))
    with connect(url) as conn:
        conn.execute("DROP SCHEMA IF EXISTS spike CASCADE")
        left = conn.execute("SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'spike'").fetchone()[0]
        conn.commit()
    print("schema spike dropped" if left == 0 else "schema spike still present")
    return 0 if left == 0 else 1
