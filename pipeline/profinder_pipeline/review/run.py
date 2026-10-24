"""`python -m profinder_pipeline review ...`
  export [--only NAME] [--out FILE]     needs_review.csv for every current candidate that needs a human + the QA sample
  import --file F [--apply]             read the reviewer columns; without --apply only reports what would happen
After an import: `match run` (re-plan with the new decisions), then `release build`. Do not repeat `extract run` in between
(it creates new candidates and the decisions stay on the old ones)."""
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..paths import OUTPUT_DIR
from ..release.run import connect_ingest, fetch_candidates
from .csvio import evaluate_row, read_csv, write_csv

_COLS = ("payload", "decision", "proposed_status", "reviewer", "reviewed_at", "review_note", "reject_reason", "qa_result")


def cmd_export(args: argparse.Namespace) -> int:
    conn = connect_ingest()
    cands = fetch_candidates(conn, args.only)
    out = Path(args.out) if args.out else OUTPUT_DIR / "review" / "needs_review.csv"
    n = write_csv(out, cands)
    print(f"{n} rows -> {out}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from psycopg.types.json import Jsonb
    conn = connect_ingest()
    rows = read_csv(Path(args.file))
    by_id = {c["candidate_id"]: c for c in fetch_candidates(conn)}
    now = datetime.now(timezone.utc)
    tally: Counter = Counter()
    for row in rows:
        cand = by_id.get(row.get("candidate_id", ""))
        page_text = None
        if cand and cand.get("page_id"):
            r = conn.execute("SELECT markdown FROM ingest.page WHERE page_id = %s::uuid", (cand["page_id"],)).fetchone()
            page_text = r[0] if r else None
        out = evaluate_row(row, cand, page_text, now)
        if not row.get("decision") and not row.get("qa_result"):
            continue
        tally["ok" if out.ok else "refused"] += 1
        print(("OK      " if out.ok else "REFUSED ") + f"{row.get('name_or_title', '')} ({row.get('candidate_id', '')[:8]}): {out.message}")
        if out.ok and args.apply and out.update:
            sets, vals = [], []
            for k in _COLS:
                if k in out.update:
                    sets.append(f"{k} = %s")
                    vals.append(Jsonb(out.update[k]) if k == "payload" else out.update[k])
            if "extra_check" in out.update:
                sets.append("checks = checks || %s")
                vals.append(Jsonb([out.update["extra_check"]]))
            conn.execute(f"UPDATE ingest.candidate SET {', '.join(sets)} WHERE candidate_id = %s::uuid", (*vals, out.candidate_id))
    if args.apply:
        conn.commit()
    print(dict(tally), "| applied" if args.apply else "| preview only (add --apply)")
    return 0 if not tally["refused"] else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline review", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--only", action="append", default=[])
    e.add_argument("--out")
    i = sub.add_parser("import")
    i.add_argument("--file", required=True)
    i.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    return {"export": cmd_export, "import": cmd_import}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
