"""Legacy audit (task 6.2, doc 02 section 8): a READ-ONLY report of existing rows that have no official source or look broken.
Nothing is written to any database (the connection is opened read-only; the rules below are pure functions over rows).
`python -m profinder_pipeline audit legacy [--out DIR]` -> legacy_audit.csv (one line per professor and reason) + legacy_audit.md.
The owner decides what to do with these rows; the pipeline never deletes or merges them."""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Optional

from ..match.modes import shared_profile_urls, _norm
from ..paths import OUTPUT_DIR
from ..utils.placeholders import PlaceholderRegistry

OFFICIAL = ("official_faculty_page", "official_import")
COLUMNS = ["professor_id", "university", "name", "nameZh", "dataSource", "dataStatus", "reason", "group_key", "other_ids", "detail"]
REASONS = ("openalex_source", "no_source", "duplicate_email", "duplicate_name_zh", "shared_directory_url", "placeholder_position",
           "placeholder_interests", "not_official_source")


def audit_professors(profs: list, uni_name: dict, registry: PlaceholderRegistry) -> list:
    """profs: Professor rows (dicts). Returns one dict per (professor, reason)."""
    out: list[dict[str, Any]] = []

    def add(p: dict, reason: str, key: str = "", others: Optional[list] = None, detail: str = "") -> None:
        out.append({"professor_id": p["id"], "university": uni_name.get(p.get("universityId"), p.get("universityId")), "name": p.get("name") or "",
                    "nameZh": p.get("nameZh") or "", "dataSource": p.get("dataSource") or "", "dataStatus": p.get("dataStatus") or "",
                    "reason": reason, "group_key": key, "other_ids": " ".join(others or []), "detail": detail})

    by_email: dict[str, list] = defaultdict(list)
    by_zh: dict[tuple, list] = defaultdict(list)
    by_uni: dict[str, list] = defaultdict(list)
    for p in profs:
        e = (p.get("email") or "").strip().lower()
        if "@" in e:
            by_email[e].append(p)
        if p.get("nameZh") and str(p["nameZh"]).strip():
            by_zh[(p.get("universityId"), _norm("nameZh", p["nameZh"]))].append(p)
        by_uni[p.get("universityId")].append(p)
    shared = {uid: shared_profile_urls(rows) for uid, rows in by_uni.items()}

    for p in profs:
        src = (p.get("dataSource") or "").strip()
        if src == "openalex":
            add(p, "openalex_source", detail="created from an OpenAlex author, not from an official page")
        elif not src:
            add(p, "no_source", detail="no dataSource (looks like a seed sample)")
        elif src not in OFFICIAL:
            add(p, "not_official_source", detail=f"dataSource={src}")
        e = (p.get("email") or "").strip().lower()
        if e in by_email and len(by_email[e]) > 1:
            add(p, "duplicate_email", e, [q["id"] for q in by_email[e] if q["id"] != p["id"]], f"{len(by_email[e])} rows share this email")
        if p.get("nameZh") and str(p["nameZh"]).strip():
            g = by_zh[(p.get("universityId"), _norm("nameZh", p["nameZh"]))]
            if len(g) > 1:
                add(p, "duplicate_name_zh", p["nameZh"], [q["id"] for q in g if q["id"] != p["id"]], f"{len(g)} rows with this Chinese name at one university")
        url = p.get("profileUrl")
        if isinstance(url, str) and url.strip() and _norm("profileUrl", url) in shared.get(p.get("universityId"), frozenset()):
            add(p, "shared_directory_url", url, [], "the same profileUrl is used by several professors of this university")
        if registry.is_placeholder("Professor", "position", p.get("position")):
            add(p, "placeholder_position", "", [], str(p.get("position")))
        if registry.is_placeholder("Professor", "researchInterests", p.get("researchInterests")):
            add(p, "placeholder_interests", "", [], str(p.get("researchInterests"))[:80])
    out.sort(key=lambda r: (r["reason"], r["university"] or "", r["group_key"], r["name"], r["professor_id"]))
    return out


def audit_templates(programs: list, scholarships: list, registry: PlaceholderRegistry) -> dict:
    """Counts only (programs/scholarships are templates, doc 01 finding 9)."""
    return {
        "programs": len(programs),
        "programs_without_programUrl": sum(1 for r in programs if not (r.get("programUrl") or "").strip()),
        "programs_placeholder_deadline": sum(1 for r in programs if registry.is_placeholder("Program", "deadline", r.get("deadline"))),
        "scholarships": len(scholarships),
        "scholarships_placeholder_deadline": sum(1 for r in scholarships if registry.is_placeholder("Scholarship", "deadline", r.get("deadline"))),
        "scholarships_not_verified": sum(1 for r in scholarships if str(r.get("verificationStatus") or "").upper() != "VERIFIED"),
    }


def to_csv(rows: list) -> str:
    b = io.StringIO()
    w = csv.DictWriter(b, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return b.getvalue()


def summary_md(rows: list, profs: list, templates: dict) -> str:
    by_reason = Counter(r["reason"] for r in rows)
    rows_with_issue = len({r["professor_id"] for r in rows})
    dup_groups: dict[tuple, set] = defaultdict(set)
    for r in rows:
        if r["reason"] == "duplicate_email":
            dup_groups[(r["university"], r["group_key"])].add(r["professor_id"])
    per_uni = Counter(u for (u, _k) in dup_groups)
    src = Counter((p.get("dataSource") or "(none)", p.get("dataStatus") or "") for p in profs)
    lines = ["# Legacy audit (read-only)", "", f"Professors in the database: {len(profs)} · with at least one finding: {rows_with_issue}", "", "## Findings by reason", ""]
    lines += [f"- {k}: {by_reason.get(k, 0)}" for k in REASONS]
    lines += ["", f"## Duplicate-email groups: {len(dup_groups)}", ""]
    lines += [f"- {u}: {n} group(s)" for u, n in per_uni.most_common()]
    lines += ["", "## Professors by dataSource / dataStatus", ""] + [f"- {s} / {st}: {n}" for (s, st), n in sorted(src.items())]
    lines += ["", "## Program and scholarship templates", ""] + [f"- {k}: {v}" for k, v in templates.items()]
    lines += ["", "Nothing was written to any database. Duplicates are listed, never merged; the owner decides.", ""]
    return "\n".join(lines)


def run(out_dir=None) -> int:
    from ..db.connection import connect
    from ..db.hostguard import url_host
    from ..load.db import PgDb
    from ..settings import get_settings
    url = get_settings().app_database_url
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set in pipeline/.env")
    host = url_host(url)
    if any(w in host.lower() for w in ("prod", "production")) and os.environ.get("ALLOW_PRODUCTION_AUDIT_READ") != "1":
        raise SystemExit(f"refusing host {host}: looks like production (the owner may set ALLOW_PRODUCTION_AUDIT_READ=1; the audit is read-only)")
    print(f"app host (read-only): {host}")
    conn = connect(url)
    conn.read_only = True
    db = PgDb(conn)
    try:
        profs = db.rows('SELECT * FROM "Professor"')
        unis = {r["id"]: r["name"] for r in db.rows('SELECT id, name FROM "University"')}
        programs = db.rows('SELECT "programUrl", deadline FROM "Program"')
        schols = db.rows('SELECT deadline, "verificationStatus" FROM "Scholarship"')
    finally:
        conn.rollback()
        conn.close()
    reg = PlaceholderRegistry.from_yaml()
    rows = audit_professors(profs, unis, reg)
    out = out_dir or OUTPUT_DIR / "audit"
    out.mkdir(parents=True, exist_ok=True)
    (out / "legacy_audit.csv").write_text(to_csv(rows), encoding="utf-8-sig")
    md = summary_md(rows, profs, audit_templates(programs, schols, reg))
    (out / "legacy_audit.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out / 'legacy_audit.csv'} ({len(rows)} lines), {out / 'legacy_audit.md'}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline audit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("legacy", help="read-only CSV of legacy rows without an official source or with defects")
    p.add_argument("--out")
    a = ap.parse_args(argv)
    from pathlib import Path
    return run(Path(a.out) if a.out else None)


if __name__ == "__main__":
    sys.exit(main())
