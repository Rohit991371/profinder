"""Task 0.5 — compare candidates with ground truth (data/official-faculty/faculty-test-5.json) and write report.md.

Usage (from pipeline/):  python -m spike.compare
"""
from __future__ import annotations

import json
from collections import Counter

from .common import INPUTS_DIR, LIST_URL, OUT_DIR, UNIVERSITY, ensure_dirs
from .htmlparse import INFO_RE
from .textutils import EMAIL_STRICT_RE, canonical_url


def _load_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pct(a: int, b: int) -> str:
    return f"{(100 * a / b):.1f}%" if b else "n/a"


def main() -> None:
    ensure_dirs()
    gt = json.loads((INPUTS_DIR / "faculty-test-5.json").read_text(encoding="utf-8"))["records"][UNIVERSITY]
    cands = _load_jsonl(OUT_DIR / "candidates.jsonl")

    gt_by_url = {canonical_url(r["profileUrl"]): r for r in gt if r.get("profileUrl")}
    cand_by_url = {canonical_url(c["profile_url"]): c for c in cands if c.get("profile_url")}
    found = sorted(set(gt_by_url) & set(cand_by_url))
    missing = sorted(set(gt_by_url) - set(cand_by_url))
    extra = sorted(set(cand_by_url) - set(gt_by_url))
    found_not_rejected = [u for u in found if cand_by_url[u]["decision"] != "rejected"]

    # emails
    gt_with_email = [u for u in found if gt_by_url[u].get("email")]
    raw_match = [u for u in gt_with_email if (cand_by_url[u].get("email_seen_on_page") or "") == gt_by_url[u]["email"].lower()]
    kept_match = [u for u in gt_with_email if (cand_by_url[u].get("email") or "") == gt_by_url[u]["email"].lower()]
    mismatches = [(u, gt_by_url[u]["email"], cand_by_url[u].get("email_seen_on_page")) for u in gt_with_email if u not in raw_match]
    dropped_domains = Counter(c["email_seen_on_page"].rsplit("@", 1)[-1] for c in cands
                              if c.get("email_seen_on_page") and not c.get("email"))
    invalid_emails = [c["name_zh"] for c in cands if c.get("email") and not EMAIL_STRICT_RE.match(c["email"])]
    shared_urls = [c["name_zh"] for c in cands if c.get("profile_url")
                   and (not INFO_RE.search(c["profile_url"]) or canonical_url(c["profile_url"]) == canonical_url(LIST_URL))]
    pos_mismatch = [(cand_by_url[u]["name_zh"], gt_by_url[u].get("position"), cand_by_url[u]["position_zh"])
                    for u in found if (gt_by_url[u].get("position") or "") != (cand_by_url[u]["position_zh"] or "")]

    groq = _read_json(OUT_DIR / "groq_result.json")
    land = _read_json(OUT_DIR / "land_result.json")
    checks = _read_json(OUT_DIR / "profile_checks.json") or []

    go_parsed = len(found) / len(gt_by_url) >= 0.80 if gt_by_url else False
    go = {
        "report exists": True,
        f"parsed >= 80% of ground truth ({pct(len(found), len(gt_by_url))})": go_parsed,
        "no invalid emails": not invalid_emails,
        "no shared-directory profileUrl": not shared_urls,
        "Groq returned valid JSON": bool(groq and groq.get("valid_json")),
        "rows in DB == JSONL lines": bool(land and land.get("db_rows") == land.get("jsonl_lines") and land.get("db_rows")),
    }

    L: list[str] = ["# Spike report — Tsinghua CS faculty", "",
                    f"Ground truth: **{len(gt)}** records ({len(gt_by_url)} unique profile URLs). "
                    f"Parsed from the list page: **{len(cands)}** entries.", "",
                    "## Coverage vs ground truth", "",
                    f"- Found by profile URL (before role filter): **{len(found)} / {len(gt_by_url)}** ({pct(len(found), len(gt_by_url))})",
                    f"- Found and not rejected by the role rule: {len(found_not_rejected)} / {len(gt_by_url)} ({pct(len(found_not_rejected), len(gt_by_url))})",
                    f"- In ground truth but missing from our parse: {len(missing)}",
                    f"- Parsed but not in ground truth: {len(extra)}", ""]
    if missing:
        L += ["Missing (first 15):"] + [f"- {u} ({gt_by_url[u]['name']}, {gt_by_url[u].get('position')})" for u in missing[:15]] + [""]
    if extra:
        L += ["Extra (first 15):"] + [f"- {u} ({cand_by_url[u]['name_zh']}, {cand_by_url[u]['position_zh']}, {cand_by_url[u]['decision']})" for u in extra[:15]] + [""]
    L += ["## Decisions", "", "| decision | count |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(Counter(c['decision'] for c in cands).items())]
    L += ["", "Reasons (top):", ""]
    L += [f"- {k}: {v}" for k, v in Counter(r.split(':')[0] for c in cands for r in c['reasons']).most_common(8)]
    L += ["", "## Emails", "",
          f"- Ground-truth people with an email that we found: {len(gt_with_email)}",
          f"- Page email equals ground truth: **{len(raw_match)}** ({pct(len(raw_match), len(gt_with_email))})",
          f"- Kept after the university-domain rule and equal: {len(kept_match)} ({pct(len(kept_match), len(gt_with_email))})",
          f"- Emails dropped by the domain rule: {sum(dropped_domains.values())} — domains: {dict(dropped_domains)}",
          f"- Invalid emails in output: **{len(invalid_emails)}**",
          f"- Shared-directory URLs stored as profileUrl: **{len(shared_urls)}**",
          f"- Position text differs from ground truth: {len(pos_mismatch)}", ""]
    if mismatches:
        L += ["Email mismatches (first 10):"] + [f"- {u}: ground truth {a} vs page {b}" for u, a, b in mismatches[:10]] + [""]
    L += ["## Profile pages checked", ""] + [f"- {c}" for c in checks] + [""]
    L += ["## Groq smoke test", "", f"```json\n{json.dumps(groq, ensure_ascii=False, indent=2) if groq else 'not run yet'}\n```", "",
          "## Database", "", f"```json\n{json.dumps(land, indent=2) if land else 'not run yet'}\n```", "",
          "## Go / no-go for Iteration 1", ""]
    L += [f"- [{'x' if ok else ' '}] {k}" for k, ok in go.items()]
    (OUT_DIR / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"found {len(found)}/{len(gt_by_url)} ({pct(len(found), len(gt_by_url))}); missing {len(missing)}; extra {len(extra)}")
    print(f"emails equal: {len(raw_match)}/{len(gt_with_email)}; dropped by domain rule: {sum(dropped_domains.values())} {dict(dropped_domains)}")
    print(f"invalid emails: {len(invalid_emails)}; shared-directory URLs: {len(shared_urls)}")
    for k, ok in go.items():
        print(("PASS " if ok else "TODO ") + k)
    print(f"report: {OUT_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
