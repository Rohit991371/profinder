#!/usr/bin/env python3
"""One-off helper: insert docs/entries/entry-03.md into pipeline/INSTRUCTIONS.md (before Part D) and add the new
cheat-sheet rows. Idempotent. Usage (from the repo root): python pipeline/scripts/append_instructions_entry.py"""
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
target = PIPE / "INSTRUCTIONS.md"
entry = (PIPE / "docs" / "entries" / "entry-03.md").read_text(encoding="utf-8").strip() + "\n"
text = target.read_bytes().decode("utf-8")
nl = "\r\n" if "\r\n" in text else "\n"
text = text.replace("\r\n", "\n")
if "### Entry 03" in text:
    print("Entry 03 already present; nothing changed")
    sys.exit(0)
marker = "\n---\n\n## Part D"
if marker not in text:
    sys.exit("Could not find '## Part D'; append the entry by hand")
text = text.replace(marker, "\n" + entry + marker, 1)
rows = [
    "| Package help | `cd pipeline && python -m profinder_pipeline --help` | 1 |",
    "| Create `ingest` schema | `python -m profinder_pipeline init-db` | 1 |",
    "| Create app tables (local DB) | `python -m profinder_pipeline app-schema` | 1 |",
    "| Seed content snapshot | `python -m profinder_pipeline seed-snapshot` | 1 |",
    "| Health check | `python -m profinder_pipeline doctor [--online]` | 1 |",
    "| Scope guard | `python pipeline/scripts/check_scope.py` | 1 |",
    "| All tests | `cd pipeline && python -m pytest tests -q` | 1 |",
    "| Trial A steps | `python -m profinder_pipeline trial-tsinghua parse --offline` / `compare` / `land` | 1 |",
]
text = text.rstrip("\n") + "\n" + "\n".join(rows) + "\n"
target.write_bytes(text.replace("\n", nl).encode("utf-8"))
print("Entry 03 and cheat-sheet rows added to", target)
