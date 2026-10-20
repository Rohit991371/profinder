#!/usr/bin/env python3
"""Thin wrapper: python pipeline/scripts/seed_snapshot.py [--replace] [--dry-run]  (same as `python -m profinder_pipeline seed-snapshot`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profinder_pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["seed-snapshot", *sys.argv[1:]]))
