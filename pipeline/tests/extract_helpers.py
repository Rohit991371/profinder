"""Shared helpers for the Iteration 4 tests (fixtures = real saved pages, scripts/styles stripped)."""
from datetime import datetime, timezone
from pathlib import Path

import yaml

from profinder_pipeline.extract.css import ProfileData, parse_list, parse_profile, visible_text
from profinder_pipeline.extract.sitecfg import load_site_configs
from profinder_pipeline.validate.professor_logic import Context, SourcePage

FIX = Path(__file__).parent / "fixtures" / "extract"
CONFIG = Path(__file__).resolve().parent.parent / "config"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
TSINGHUA, SJTU, ECNU = "Tsinghua University", "Shanghai Jiao Tong University", "East China Normal University"


def html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def cfg(university: str):
    return load_site_configs()[university]


def domains(university: str) -> tuple[list[str], list[str]]:
    d = yaml.safe_load((CONFIG / "domains.yml").read_text(encoding="utf-8"))["universities"][university]
    return d["domains"], (d.get("email_domains") or d["domains"])


def fake_romanise(name: str):
    """pypinyin is not needed to test validation logic."""
    if name and all("\u3400" <= c <= "\u9fff" for c in name):
        return "Fam Giv", "Giv", "Fam"
    return None


def make_context(university: str, list_fixture: str, status: int = 200, **kw) -> tuple[Context, object]:
    c = cfg(university)
    doms, emails = domains(university)
    h = html(list_fixture)
    lp = parse_list(h, c)
    page = SourcePage(c.list_url, status, NOW, lp.page_text)
    return Context(university=university, site=c, domains=doms, email_domains=emails, list_page=page, **kw), lp
