"""Drift alarm: the pipeline's record models must stay in step with prisma/schema.prisma (the owner changes it often)."""
import pytest

from profinder_pipeline.db.prisma_schema import parse_models
from profinder_pipeline.paths import PRISMA_SCHEMA

pytestmark = pytest.mark.skipif(not PRISMA_SCHEMA.exists(), reason="prisma/schema.prisma not found (run inside the repo)")


def _models():
    return parse_models(PRISMA_SCHEMA.read_text(encoding="utf-8"))


def test_prisma_parser_sees_the_entities_we_load():
    m = _models()
    for name in ("University", "Professor", "Program", "Scholarship", "SourceRecord", "ChangeLog", "ProfessorSource"):
        assert name in m, name
    assert "nameZh" in m["Professor"].column_names()
    assert "researchAreas" not in m["Professor"].column_names()      # relation, not a column


def test_record_models_match_prisma_columns():
    pytest.importorskip("pydantic")
    from profinder_pipeline.schemas.entities import (IGNORED_COLUMNS, LOADER_SUPPLIED_COLUMNS, NON_COLUMN_FIELDS, PRISMA_MODEL, RECORD_MODELS)
    models = _models()
    for entity, record in RECORD_MODELS.items():
        cols = set(models[PRISMA_MODEL[entity]].column_names())
        fields = set(record.model_fields) - NON_COLUMN_FIELDS.get(entity, frozenset())
        assert fields <= cols, f"{record.__name__} has fields that are not Prisma columns: {sorted(fields - cols)}"
        ignored = IGNORED_COLUMNS.get(entity, frozenset())
        assert ignored <= cols, f"stale IGNORED_COLUMNS for {entity}: {sorted(ignored - cols)}"
        unhandled = cols - fields - LOADER_SUPPLIED_COLUMNS - ignored
        assert not unhandled, (f"Prisma {PRISMA_MODEL[entity]} has new columns the pipeline does not know: {sorted(unhandled)}. "
                               "Add them to the record model (or IGNORED_COLUMNS with a reason).")


def test_required_columns_without_default_are_covered():
    pytest.importorskip("pydantic")
    from profinder_pipeline.schemas.entities import LOADER_SUPPLIED_COLUMNS, PRISMA_MODEL, RECORD_MODELS
    models = _models()
    for entity, record in RECORD_MODELS.items():
        for col in models[PRISMA_MODEL[entity]].columns:
            if not col.optional and not col.has_default and not col.is_updated_at:
                assert col.name in record.model_fields or col.name in LOADER_SUPPLIED_COLUMNS, f"{PRISMA_MODEL[entity]}.{col.name}"
