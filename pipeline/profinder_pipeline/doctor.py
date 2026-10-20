"""`doctor`: environment health check. Prints host names and set/missing flags only - never secret values."""
import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .db.hostguard import host_label
from .db.migrate import EXPECTED_TABLES
from .paths import PIPELINE_DIR, PRISMA_SCHEMA, REPO_ROOT
from .settings import env_presence, get_settings

PACKAGES = ("pydantic", "pypinyin", "bs4", "lxml", "yaml", "dotenv", "httpx", "psycopg")
KEY_APP_TABLES = ("University", "Professor", "Program", "Scholarship", "SourceRecord", "ChangeLog", "College", "Department")


@dataclass
class Check:
    status: str   # PASS | WARN | FAIL | INFO
    name: str
    detail: str = ""


def _tool_version(cmd: list[str]) -> Optional[str]:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "found (version unreadable)"


def run_checks(online: bool = False) -> list[Check]:
    s = get_settings()
    out: list[Check] = []
    v = sys.version_info
    out.append(Check("PASS" if v >= (3, 12) else "WARN", "python", f"{v.major}.{v.minor}.{v.micro} (repo workflows use 3.12)"))

    missing = []
    for pkg in PACKAGES:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    out.append(Check("FAIL" if missing else "PASS", "python packages", ("missing: " + ", ".join(missing) + " (pip install -r requirements.txt)") if missing else "all importable"))

    for name, is_set, required in env_presence():
        out.append(Check("PASS" if is_set else ("FAIL" if required else "INFO"), f"env {name}", "set" if is_set else "not set"))

    for label, url in (("pipeline DB", s.pipeline_database_url), ("app DB", s.app_database_url)):
        out += _db_checks(label, url)

    for cmd, required in ((["node", "--version"], False), (["npx", "--version"], False), (["docker", "--version"], False), (["git", "--version"], True)):
        ver = _tool_version(cmd)
        out.append(Check("PASS" if ver else ("FAIL" if required else "WARN"), f"tool {cmd[0]}", ver or "not found on PATH"))

    if PRISMA_SCHEMA.exists():
        from .db.prisma_schema import datasource_provider, parse_models
        text = PRISMA_SCHEMA.read_text(encoding="utf-8")
        out.append(Check("PASS", "prisma/schema.prisma", f"{len(parse_models(text))} models, provider {datasource_provider(text)} (read-only)"))
    else:
        out.append(Check("WARN", "prisma/schema.prisma", f"not found at {PRISMA_SCHEMA.relative_to(REPO_ROOT)}"))

    out.append(_scope_check())
    if online:
        out += _online_checks(s.groq_api_key)
    return out


def _db_checks(label: str, url: Optional[str]) -> list[Check]:
    if not url:
        return [Check("FAIL", f"{label} reachable", "URL not set")]
    try:
        from .db.connection import connect
        with connect(url) as conn:
            conn.execute("SELECT 1")
            out = [Check("PASS", f"{label} reachable", host_label(url))]
            if label == "pipeline DB":
                have = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='ingest'").fetchall()}
                miss = [t for t in EXPECTED_TABLES if t not in have]
                out.append(Check("PASS" if not miss else "WARN", "schema ingest", "5 tables present" if not miss else f"missing {miss} - run init-db"))
            else:
                have = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
                miss = [t for t in KEY_APP_TABLES if t not in have]
                if miss:
                    out.append(Check("WARN", "app tables", f"missing {miss} - run app-schema"))
                else:
                    n_uni = conn.execute('SELECT count(*) FROM public."University"').fetchone()[0]
                    n_prof = conn.execute('SELECT count(*) FROM public."Professor"').fetchone()[0]
                    out.append(Check("PASS", "app tables", f"present; University={n_uni}, Professor={n_prof}"))
            return out
    except Exception as exc:  # noqa: BLE001 - report, never raise from doctor
        return [Check("FAIL", f"{label} reachable", f"{type(exc).__name__} ({host_label(url)})")]


def _scope_check() -> Check:
    script = PIPELINE_DIR / "scripts" / "check_scope.py"
    try:
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("WARN", "scope guard", type(exc).__name__)
    first = (proc.stdout.strip().splitlines() or [""])[0]
    if proc.returncode == 0:
        return Check("PASS", "scope guard", first)
    return Check("WARN" if proc.returncode == 2 else "FAIL", "scope guard", first)


def _online_checks(groq_key: Optional[str]) -> list[Check]:
    out: list[Check] = []
    try:
        import httpx
        if groq_key:
            r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {groq_key}"}, timeout=20)
            out.append(Check("PASS" if r.status_code == 200 else "FAIL", "Groq API key", f"HTTP {r.status_code} (models list; no tokens used)"))
        else:
            out.append(Check("INFO", "Groq API key", "not set, skipped"))
        r = httpx.get("https://www.cs.tsinghua.edu.cn/robots.txt", timeout=20, follow_redirects=True)
        out.append(Check("PASS", "network to .edu.cn", f"HTTP {r.status_code} from www.cs.tsinghua.edu.cn"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("FAIL", "online checks", type(exc).__name__))
    return out


def doctor(online: bool = False) -> int:
    checks = run_checks(online)
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status:<4}] {c.name:<{width}}  {c.detail}")
    fails = sum(1 for c in checks if c.status == "FAIL")
    warns = sum(1 for c in checks if c.status == "WARN")
    print(f"\n{fails} fail, {warns} warn")
    return 1 if fails else 0
