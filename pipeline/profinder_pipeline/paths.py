"""Filesystem locations. Everything the pipeline writes goes under pipeline/ (output/, .scratch/)."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = PACKAGE_DIR.parent
REPO_ROOT = PIPELINE_DIR.parent

CONFIG_DIR = PIPELINE_DIR / "config"
INPUTS_DIR = PIPELINE_DIR / "inputs"
OUTPUT_DIR = PIPELINE_DIR / "output"          # git-ignored
SCRATCH_DIR = PIPELINE_DIR / ".scratch"       # git-ignored
MIGRATIONS_DIR = PACKAGE_DIR / "db" / "migrations"

# Read-only inputs that belong to the owner's repo. The pipeline never writes to these.
PRISMA_SCHEMA = REPO_ROOT / "prisma" / "schema.prisma"
DEV_DB = REPO_ROOT / "prisma" / "dev.db"
