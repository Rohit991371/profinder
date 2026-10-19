"""Task 0.3 — parse the list page (and check saved profile pages) into ProfessorCandidateV0 JSONL.

Usage (from pipeline/):  python -m spike.parse [--offline] [--extra-email-domain cernet.edu.cn]
Writes output/spike/candidates.jsonl, parse_summary.json, profile_checks.json.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter

from .common import (ALLOWED_EMAIL_DOMAINS, INPUTS_DIR, LIST_URL, OUT_DIR, SCHOOL_EN, ensure_dirs)
from .fetch import fetch_text
from .htmlparse import INFO_RE, RawEntry, parse_list_html, visible_text
from .models import ProfessorCandidateV0
from .textutils import EMAIL_STRICT_RE, canonical_url, clean_name, email_domain_ok, normalise_position

# Saved profile pages -> the URL they were downloaded from.
PROFILE_FILES = {
    "tsinghua_prof1.html": "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm",
    "tsinghua_prof2.html": "https://www.cs.tsinghua.edu.cn/info/1121/3551.htm",
}


def build_candidate(raw: RawEntry, fetched_at: str, allowed_domains: list[str]) -> ProfessorCandidateV0:
    try:
        from .names import romanise
        rom = romanise(clean_name(raw.name_raw)[0])
    except ImportError:  # pypinyin missing
        rom = None
    name_zh, note = clean_name(raw.name_raw)
    position_en, role_class = normalise_position(raw.position_zh)
    reasons: list[str] = []

    valid_emails = [e for e in raw.emails if EMAIL_STRICT_RE.match(e)]
    seen = valid_emails[0] if valid_emails else None
    domain_ok = email_domain_ok(seen, allowed_domains) if seen else None
    email = seen if domain_ok else None
    if seen and not domain_ok:
        reasons.append(f"email_domain_not_university:{seen.rsplit('@', 1)[-1]}")
    if len(valid_emails) > 1:
        reasons.append("multiple_emails_on_entry")
    if rom is None:
        reasons.append("name_not_romanised")

    if role_class == "non_faculty":
        decision = "rejected"
        reasons.append(f"non_faculty_role:{raw.position_zh}")
    elif role_class == "unknown":
        decision = "needs_review"
        reasons.append(f"unknown_position:{raw.position_zh}")
    elif note:
        decision = "needs_review"
        reasons.append(f"concurrent_post:{note}")
    else:
        decision = "auto_accept"

    personal = bool(INFO_RE.search(raw.profile_url)) and canonical_url(raw.profile_url) != canonical_url(LIST_URL)
    return ProfessorCandidateV0(
        name_zh=name_zh, name_note_zh=note,
        name=rom[0] if rom else None, first_name=rom[1] if rom else None, last_name=rom[2] if rom else None,
        position_zh=raw.position_zh, position=position_en, role_class=role_class,
        school=SCHOOL_EN, institute_zh=raw.institute_zh, rank_group_zh=raw.rank_group_zh,
        email=email, email_seen_on_page=seen, email_domain_ok=domain_ok,
        profile_url=raw.profile_url if personal else None, profile_is_personal=personal,
        source_url=LIST_URL, fetched_at=fetched_at, extractor="css", decision=decision, reasons=reasons,
    )


def check_profiles(candidates: list[ProfessorCandidateV0], offline: bool) -> list[dict[str, object]]:
    by_url = {c.profile_url: c for c in candidates if c.profile_url}
    checks: list[dict[str, object]] = []
    for fname, url in PROFILE_FILES.items():
        path = INPUTS_DIR / fname
        if not path.exists():
            checks.append({"file": fname, "status": "missing"})
            continue
        res = fetch_text(url, path, offline=offline)
        text = visible_text(res.text)
        cand = by_url.get(canonical_url(url))
        checks.append({
            "file": fname, "url": url, "source": res.source, "text_chars": len(text),
            "candidate_found_in_list": cand is not None,
            "name_zh": cand.name_zh if cand else None,
            "name_found_in_page_text": bool(cand and cand.name_zh in re.sub(r"\s+", "", text)),
            "email_found_in_page_text": bool(cand and cand.email_seen_on_page and cand.email_seen_on_page in text.lower()),
        })
    return checks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--extra-email-domain", action="append", default=[], help="additional allowed email domain (repeatable)")
    args = ap.parse_args()
    ensure_dirs()
    allowed = ALLOWED_EMAIL_DOMAINS + args.extra_email_domain

    res = fetch_text(LIST_URL, INPUTS_DIR / "tsinghua_list.html", offline=args.offline)
    parsed = parse_list_html(res.text, LIST_URL)
    candidates = [build_candidate(e, res.fetched_at, allowed) for e in parsed.entries]

    with (OUT_DIR / "candidates.jsonl").open("w", encoding="utf-8") as fh:
        for c in candidates:
            fh.write(c.model_dump_json() + "\n")

    checks = check_profiles(candidates, args.offline)
    (OUT_DIR / "profile_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "list_source": res.source, "robots": res.robots_note, "encoding": res.encoding,
        "parser_mode": parsed.mode, "entries_raw": parsed.raw_entry_count,
        "duplicate_urls_dropped": parsed.duplicate_urls, "candidates": len(candidates),
        "by_decision": dict(Counter(c.decision for c in candidates)),
        "by_position_zh": dict(Counter(c.position_zh for c in candidates)),
        "by_institute_zh": dict(Counter(c.institute_zh for c in candidates)),
        "with_email_seen": sum(1 for c in candidates if c.email_seen_on_page),
        "email_kept": sum(1 for c in candidates if c.email),
        "allowed_email_domains": allowed,
    }
    (OUT_DIR / "parse_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"list: {res.source}, parser mode '{parsed.mode}', {len(candidates)} candidates "
          f"({parsed.duplicate_urls} duplicate URLs dropped)")
    print("decisions:", summary["by_decision"])
    print(f"emails: {summary['with_email_seen']} seen on page, {summary['email_kept']} kept after domain rule")
    for ch in checks:
        print("profile check:", ch)
    for c in candidates[:3]:
        print(" sample:", c.name_zh, "|", c.name, "|", c.position, "|", c.email, "|", c.profile_url)
    print(f"written: {OUT_DIR / 'candidates.jsonl'}")


if __name__ == "__main__":
    main()
