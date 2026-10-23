"""Extraction report (task 4.8): output/extract/extraction_report.md from the JSONL files of the last `extract run`.
Recall for Tsinghua is measured against the 136-person ground truth (inputs/faculty-test-5.json), faculty only. A missing person
counts as 'absent from the saved list page' ONLY if neither their profile URL nor their name occurs anywhere in the cached list
page; everything else is an extractor miss. The ground truth is therefore independent of the extractor (it was made earlier)."""
import csv
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from ..fetch.cache import PageCache
from ..paths import INPUTS_DIR, OUTPUT_DIR
from ..utils.positions import normalise_position
from ..utils.urls import canonical_url
from ..validate.grounding import norm_text
from .css import visible_text
from .llm_core import PRICE_IN_PER_M, PRICE_OUT_PER_M
from .sitecfg import load_site_configs

OUT_DIR = OUTPUT_DIR / "extract"
GROUND_TRUTH = INPUTS_DIR / "faculty-test-5.json"
GT_UNIVERSITY = "Tsinghua University"


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()] if path.is_file() else []


def recall_vs_ground_truth(cands: list[dict[str, Any]], list_text: str) -> Optional[dict[str, Any]]:
    if not GROUND_TRUTH.is_file():
        return None
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["records"][GT_UNIVERSITY]
    page_norm = norm_text(list_text)
    kept = {canonical_url(c["payload"]["profileUrl"]): c for c in cands if c["payload"].get("profileUrl") and c["decision"] != "rejected"}
    by_name = {c["payload"].get("nameZh"): c for c in cands if c["decision"] != "rejected"}
    faculty = [r for r in gt if normalise_position(r.get("position"))[1] == "faculty"]
    found, absent, missed, email_cmp, email_same, email_diff = 0, [], [], 0, 0, []
    for r in faculty:
        c = kept.get(canonical_url(r.get("profileUrl") or "")) or by_name.get(r.get("name"))
        if c is not None:
            found += 1
            if r.get("email"):
                email_cmp += 1
                same = (c["payload"].get("email") or "").lower() == r["email"].lower()
                email_same += int(same)
                if not same:
                    email_diff.append(f"{r.get('name')}: ground truth {r['email']} / extracted {c['payload'].get('email')}")
            continue
        on_page = (canonical_url(r.get("profileUrl") or "") and (r.get("profileUrl") or "").split("/info/")[-1] in list_text) or norm_text(r.get("name")) in page_norm
        (missed if on_page else absent).append(f"{r.get('name')} {r.get('profileUrl') or ''}".strip())
    present = len(faculty) - len(absent)
    return {"ground_truth": len(gt), "faculty": len(faculty), "found": found, "absent_from_page": absent, "extractor_missed": missed,
            "recall_present": found / present if present else None, "recall_all": found / len(faculty) if faculty else None,
            "email_compared": email_cmp, "email_same": email_same, "email_diff": email_diff}


def llm_usage() -> dict[str, Any]:
    rows = _rows(OUT_DIR / "llm_calls.jsonl")
    ok = [r for r in rows if r.get("status") == "ok"]
    pt, ct = sum(r["prompt_tokens"] for r in ok), sum(r["completion_tokens"] for r in ok)
    return {"calls": len(ok), "errors": len(rows) - len(ok), "prompt_tokens": pt, "completion_tokens": ct,
            "usd": (pt * PRICE_IN_PER_M + ct * PRICE_OUT_PER_M) / 1e6, "models": sorted({r.get("model", "?") for r in rows})}


def programs_section() -> list[str]:
    """Programs/scholarships found on official pages (task 4.6). Dates, tuition and fees are listed as HELD BACK for human approval."""
    files = sorted(OUT_DIR.glob("programs_*.jsonl"))
    if not files:
        return []
    out = ["## Programs and scholarships (official pages)", "",
           "Program candidates mean only: this degree + major is named on this official page. Dates, tuition and fees are never stored; "
           "the quoted text below waits for human approval in the review step. Scholarships are always needs_review.", ""]
    for f in files:
        rows = _rows(f)
        if not rows:
            out += [f"### {f.stem.replace('programs_', '')}: no candidates", ""]
            continue
        uni = rows[0].get("university_name", f.stem)
        dec = Counter((r["entity_type"], r["decision"]) for r in rows)
        out += [f"### {uni}", "", "- " + ", ".join(f"{e} {d}: {n}" for (e, d), n in sorted(dec.items()))]
        for r in rows:
            p = r["payload"]
            title = f"{p.get('degree')} {p.get('major')}" if r["entity_type"] == "program" else f"{p.get('name')} (type {p.get('type')}, coverage {p.get('coverage')})"
            held = [f"{e['field'].split(':', 1)[1]}: \"{e['quote'][:80]}\"" for e in r.get("evidence", []) if e["field"].startswith("requires_human:")]
            out.append(f"  - [{r['entity_type']}/{r['decision']}] {title} - {r['source_url']}" + (f" - HELD BACK: {'; '.join(held)}" if held else ""))
        out.append("")
    return out


def write_report() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfgs = load_site_configs()
    lines = ["# Extraction report (Iteration 4)", ""]
    exit_notes: list[str] = []
    summary_rows: list[str] = []
    details: list[str] = []
    for uni, cfg in cfgs.items():
        cands = _rows(OUT_DIR / f"{cfg.slug}.jsonl")
        stats = json.loads((OUT_DIR / f"{cfg.slug}_stats.json").read_text(encoding="utf-8")) if (OUT_DIR / f"{cfg.slug}_stats.json").is_file() else {}
        if not cands:
            summary_rows.append(f"| {uni} | not run | | | | | | | | |")
            continue
        dec = Counter(c["decision"] for c in cands)
        fac = [c for c in cands if c["decision"] != "rejected"]
        with_email = sum(1 for c in fac if c["payload"].get("email"))
        with_int = sum(1 for c in fac if c["payload"].get("researchInterests"))
        with_area = sum(1 for c in fac if c["payload"].get("researchAreas"))
        personal = sum(1 for c in fac if c["payload"].get("profileIsPersonal"))
        summary_rows.append(f"| {uni} | {stats.get('people_parsed', len(cands))} | {dec['auto_accept']} | {dec['needs_review']} | {dec['rejected']} | "
                            f"{with_email}/{len(fac)} | {personal}/{len(fac)} | {with_int}/{len(fac)} | {with_area}/{len(fac)} | {stats.get('pending_profile', 0)} |")
        failed = Counter(ch["name"] for c in cands for ch in c["checks"] if not ch["passed"])
        reasons = Counter((c.get("reject_reason") or "").split(":")[0] + ":" + (c.get("reject_reason") or "").split(":", 1)[-1][:24]
                          for c in cands if c["decision"] == "rejected")
        areas = Counter(a for c in fac for a in c["payload"].get("researchAreas", []))
        details += [f"## {uni}", "", f"- list page: {stats.get('list_url')} (HTTP {stats.get('list_status')}, fetched {stats.get('list_fetched_at')})",
                    f"- parsed {stats.get('people_parsed')} people ({stats.get('duplicate_urls', 0)} duplicate links dropped, {stats.get('skipped_not_person_link', 0)} non-person links skipped)",
                    f"- decisions: {dict(dec)}", f"- failed checks: {dict(failed) or 'none'}", f"- reject reasons: {dict(reasons) or 'none'}",
                    f"- areas: {dict(areas.most_common(12)) or 'none'}"]
        if stats.get("pending_profile"):
            details.append(f"- **{stats['pending_profile']} people skipped: profile page not fetched yet** (position and email live on the profile page for this site; run `fetch run --only \"{uni}\" --budget 400`, then extract again)")
        rng = random.Random(11)
        sample = rng.sample(cands, min(30, len(cands)))
        with (OUT_DIR / f"spotcheck_{cfg.slug}.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["nameZh", "name", "position", "email", "profileUrl", "department", "interests", "areas", "decision", "failed_checks", "source_url", "reviewer_ok(y/n)", "note"])
            for c in sample:
                p = c["payload"]
                w.writerow([p.get("nameZh"), p.get("name"), p.get("position"), p.get("email"), p.get("profileUrl"), p.get("department"),
                            p.get("researchInterests"), ";".join(p.get("researchAreas", [])), c["decision"],
                            ";".join(ch["name"] for ch in c["checks"] if not ch["passed"]), c["source_url"], "", ""])
        details += [f"- hand-check sample (30, fixed seed): `spotcheck_{cfg.slug}.csv`", ""]
        if uni == GT_UNIVERSITY:
            lc = PageCache(INPUTS_DIR / "cache" / "pages").read(cfg.list_url, allow_stale=True)
            rec = recall_vs_ground_truth(cands, visible_text(lc.html) if lc else "")
            if rec:
                rp = rec["recall_present"]
                details += ["### Recall vs the 136-person ground truth (faculty only)", "",
                            f"- faculty in ground truth: {rec['faculty']} of {rec['ground_truth']} (the rest are engineers/technicians)",
                            f"- found: {rec['found']}; recall on people present on the saved page: {rp:.1%}; recall on all ground-truth faculty: {rec['recall_all']:.1%}",
                            f"- absent from the saved list page (list changed since the ground truth): {rec['absent_from_page'] or 'none'}",
                            f"- extractor misses: {rec['extractor_missed'] or 'none'}",
                            f"- emails equal for {rec['email_same']} of {rec['email_compared']} compared; differences: {rec['email_diff'] or 'none'}", ""]
                exit_notes.append(f"Trial A recall (present on page) {rp:.1%} - {'PASS' if rp is not None and rp >= 0.95 else 'FAIL'} (target >= 95%)")
    prog_lines = programs_section()
    u = llm_usage()
    lines += ["| university | parsed | auto_accept | needs_review | rejected | email | personal URL | interests | areas | waiting for profile |",
              "|---|---|---|---|---|---|---|---|---|---|", *summary_rows, "", *details, *prog_lines,
              "## Groq usage", "", f"- calls {u['calls']} (errors {u['errors']}), tokens {u['prompt_tokens']} in + {u['completion_tokens']} out, "
              f"estimated cost USD {u['usd']:.4f}, models {u['models'] or 'none'}",
              "- CSS needs no LLM tokens; Groq is only used for research interests where a page has no interests section (`--llm`).", ""]
    if exit_notes:
        lines += ["## Exit criteria", ""] + [f"- {n}" for n in exit_notes] + [""]
    (OUT_DIR / "extraction_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("written:", OUT_DIR / "extraction_report.md")
    for n in exit_notes:
        print(n)
    return 0
