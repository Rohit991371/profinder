"""Per-site extraction configs: config/sites/<slug>.yml (task 4.1). One file per university faculty list."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from ..paths import CONFIG_DIR

SITES_DIR = CONFIG_DIR / "sites"


@dataclass
class SiteConfig:
    slug: str
    university: str
    list_url: str
    school_en: Optional[str]
    school_zh: Optional[str]
    render: str
    personal_url_patterns: list[str]
    list_spec: dict[str, Any]
    profile_spec: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False        # set by `site-learn`; a human removes it after checking the selectors

    @property
    def profile_required(self) -> bool:
        return bool(self.profile_spec.get("required", False))

    @property
    def min_items(self) -> int:
        return int(self.list_spec.get("min_items", 1))


def load_site_config(path: Path) -> SiteConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("university", "list_url", "list"):
        if key not in data:
            raise ValueError(f"{path.name}: missing '{key}'")
    lst = data["list"]
    if "item" not in lst or "fields" not in lst:
        raise ValueError(f"{path.name}: list needs 'item' and 'fields'")
    return SiteConfig(
        slug=path.stem, university=data["university"], list_url=data["list_url"],
        school_en=data.get("school_en"), school_zh=data.get("school_zh"), render=data.get("render", "static"),
        personal_url_patterns=list(data.get("personal_url_patterns") or []),
        list_spec=lst, profile_spec=data.get("profile") or {"required": False, "fields": {}}, raw=data,
        needs_review=bool(data.get("needs_review", False)))


def load_site_configs(directory: Path = SITES_DIR, include_unreviewed: bool = False) -> dict[str, SiteConfig]:
    """{university name: SiteConfig}. Files starting with '_' and sub-folders (config/sites/_proposed/) are ignored; a config that
    still says `needs_review: true` is skipped until a human has checked it."""
    out: dict[str, SiteConfig] = {}
    if directory.is_dir():
        for p in sorted(directory.glob("*.yml")):
            if p.name.startswith("_"):
                continue
            cfg = load_site_config(p)
            if cfg.needs_review and not include_unreviewed:
                continue
            out[cfg.university] = cfg
    return out
