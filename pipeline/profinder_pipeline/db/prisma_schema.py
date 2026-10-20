"""Minimal reader for prisma/schema.prisma (read-only) + the scratch Postgres copy used by `app-schema`.
Never writes to prisma/. Enough for the contract test and the provider swap; it is not a full Prisma parser."""
import re
from dataclasses import dataclass, field

SCALARS = {"String", "Int", "BigInt", "Float", "Decimal", "Boolean", "DateTime", "Json", "Bytes"}
_MODEL_RE = re.compile(r"^model\s+(\w+)\s*\{(.*?)^\}", re.M | re.S)
_ENUM_RE = re.compile(r"^enum\s+(\w+)\s*\{", re.M)
_FIELD_RE = re.compile(r"^(\w+)\s+(\w+)(\[\])?(\?)?(.*)$")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    optional: bool
    has_default: bool
    is_updated_at: bool
    is_id: bool


@dataclass
class Model:
    name: str
    columns: list[Column] = field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def parse_models(text: str) -> dict[str, Model]:
    """Scalar (non-relation) columns of every model. Relation fields (type = another model) are skipped."""
    text = text.replace("\r\n", "\n")
    enums = set(_ENUM_RE.findall(text))
    scalar_types = SCALARS | enums
    models: dict[str, Model] = {}
    for name, body in _MODEL_RE.findall(text):
        model = Model(name)
        for raw in body.splitlines():
            line = raw.split("//", 1)[0].strip()
            if not line or line.startswith("@@"):
                continue
            m = _FIELD_RE.match(line)
            if not m:
                continue
            fname, ftype, is_list, opt, rest = m.groups()
            if is_list or ftype not in scalar_types:
                continue
            model.columns.append(Column(fname, ftype, bool(opt), "@default(" in rest, "@updatedAt" in rest, "@id" in rest))
        models[name] = model
    return models


_DATASOURCE_RE = re.compile(r"(datasource\s+db\s*\{)(.*?)(\})", re.S)
_PROVIDER_RE = re.compile(r'provider\s*=\s*"(?:sqlite|postgresql)"')
_BINARY_TARGETS_RE = re.compile(r"^\s*binaryTargets\s*=.*\n", re.M)


def to_postgres_schema(text: str) -> str:
    """Copy of the schema with datasource provider = postgresql (the generator block is left alone).
    binaryTargets is dropped from the scratch copy: db push never generates a client, and extra engines would be downloaded for nothing."""
    text = text.replace("\r\n", "\n")
    m = _DATASOURCE_RE.search(text)
    if not m or not _PROVIDER_RE.search(m.group(2)):
        raise ValueError("datasource block with provider sqlite/postgresql not found in schema.prisma")
    body = _PROVIDER_RE.sub('provider = "postgresql"', m.group(2), count=1)
    out = text[: m.start()] + m.group(1) + body + m.group(3) + text[m.end():]
    return _BINARY_TARGETS_RE.sub("", out)


def datasource_provider(text: str) -> str:
    m = _DATASOURCE_RE.search(text.replace("\r\n", "\n"))
    p = _PROVIDER_RE.search(m.group(2)) if m else None
    return p.group(0).split('"')[1] if p else "unknown"
