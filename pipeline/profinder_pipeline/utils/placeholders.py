"""Placeholder detector (doc 01 section 8.5). Rules live in config/placeholders.yml; `enrich` treats a placeholder as empty."""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from ..paths import CONFIG_DIR
from .text import is_cjk_only

KINDS = ("equals", "contains", "starts_with", "regex", "cjk_only")


@dataclass(frozen=True)
class PlaceholderRule:
    entity: str      # Prisma model name, e.g. "Professor"
    field: str       # Prisma column, e.g. "researchInterests"
    kind: str
    value: Optional[str] = None
    note: str = ""

    def matches(self, text: str) -> bool:
        t = text.strip()
        if self.kind == "cjk_only":
            return is_cjk_only(t)
        v = self.value or ""
        if self.kind == "equals":
            return t.casefold() == v.casefold()
        if self.kind == "contains":
            return v.casefold() in t.casefold()
        if self.kind == "starts_with":
            return t.casefold().startswith(v.casefold())
        return re.search(v, t, re.I | re.S) is not None  # regex


class PlaceholderRegistry:
    def __init__(self, rules: Iterable[PlaceholderRule]):
        self.rules = list(rules)
        for r in self.rules:
            if r.kind not in KINDS:
                raise ValueError(f"unknown placeholder kind {r.kind!r} for {r.entity}.{r.field}")
            if r.kind != "cjk_only" and not r.value:
                raise ValueError(f"placeholder rule {r.entity}.{r.field} ({r.kind}) needs a value")
            if r.kind == "regex":
                re.compile(r.value or "")

    @classmethod
    def from_yaml(cls, path: Optional[Path] = None) -> "PlaceholderRegistry":
        data = yaml.safe_load((path or CONFIG_DIR / "placeholders.yml").read_text(encoding="utf-8")) or {}
        return cls(PlaceholderRule(entity=r["entity"], field=r["field"], kind=r["kind"],
                                   value=r.get("value"), note=r.get("note", "")) for r in data.get("rules", []))

    def is_placeholder(self, entity: str, field: str, value: Any) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        return any(r.matches(value) for r in self.rules if r.entity == entity and r.field == field)

    def is_fillable(self, entity: str, field: str, value: Any) -> bool:
        """True if `enrich` may write this field: NULL, empty/whitespace, or a known placeholder."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return True
        return self.is_placeholder(entity, field, value)

    def placeholder_fields(self, entity: str, record: dict[str, Any]) -> list[str]:
        """Names of fields in `record` whose value is a placeholder (blocking check B5 on a candidate's stored fields)."""
        return sorted(f for f, v in record.items() if self.is_placeholder(entity, f, v))
