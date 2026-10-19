"""Paths and env loading shared by the spike scripts. Never prints secrets."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parents[1]
INPUTS_DIR = PIPELINE_DIR / "inputs"
# output/ is git-ignored (INSTRUCTIONS Part B "Never commit"), so spike results can never be committed by accident.
OUT_DIR = PIPELINE_DIR / "output" / "spike"
CACHE_DIR = OUT_DIR / "cache"

load_dotenv(PIPELINE_DIR / ".env")

LIST_URL = "https://www.cs.tsinghua.edu.cn/szzk/jzgml.htm"
UNIVERSITY = "Tsinghua University"
# English name of the department (spike constant; Iteration 2 moves this to config/universities.yml).
SCHOOL_EN = "Department of Computer Science and Technology"
ALLOWED_EMAIL_DOMAINS = ["tsinghua.edu.cn"]


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default
