import re
import sqlite3
from datetime import datetime

import pytest

from profinder_pipeline.db.migrate import list_migrations, sql_checksum
from profinder_pipeline.db.hostguard import HostRefused, host_label, is_local, refuse_production, require_local
from profinder_pipeline.db.prisma_schema import datasource_provider, parse_models, to_postgres_schema
from profinder_pipeline.db import seed_snapshot as ss
from profinder_pipeline.paths import MIGRATIONS_DIR, PRISMA_SCHEMA
from profinder_pipeline.schemas import enums

SQL = (MIGRATIONS_DIR / "001_ingest_schema.sql").read_text(encoding="utf-8")


def test_migration_defines_the_five_tables_and_only_ingest():
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\.(\w+)", SQL)
    assert sorted(t for _, t in tables) == ["candidate", "page", "release", "run", "target"]
    assert {s for s, _ in tables} == {"ingest"}
    assert "public." not in SQL and '"University"' not in SQL and "DROP " not in SQL.upper() and "DELETE " not in SQL.upper()


def test_every_enum_value_is_allowed_by_a_check_constraint():
    for enum_cls in (enums.EntityType, enums.ProposedStatus, enums.Decision, enums.Action, enums.Extractor, enums.TargetKind,
                     enums.TargetStatus, enums.RobotsStatus, enums.DiscoveredBy, enums.RenderMode, enums.RunKind,
                     enums.RunStatus, enums.ReleaseStatus):
        for member in enum_cls:
            assert f"'{member.value}'" in SQL, f"{enum_cls.__name__}.{member.name} missing from the SQL CHECK constraints"


def test_migration_listing_and_crlf_insensitive_checksum():
    assert [m.version for m in list_migrations()] == ["001_ingest_schema", "002_current_candidate_view", "003_qa_columns"]
    assert sql_checksum("a\r\nb\r\n") == sql_checksum("a\nb\n")
    assert len(list_migrations()[0].checksum) == 64


def test_bad_migration_name_is_refused(tmp_path):
    (tmp_path / "oops.sql").write_text("select 1", encoding="utf-8")
    with pytest.raises(ValueError):
        list_migrations(tmp_path)


def test_host_guards():
    assert is_local("postgresql://u:p@localhost:55432/db") and not is_local("postgresql://u:p@ep-x.neon.tech/db")
    assert host_label("postgresql://u:secret@localhost:55432/db") == "localhost:55432"
    assert require_local("postgresql://u:p@127.0.0.1:55432/db", "x") == "127.0.0.1:55432"
    with pytest.raises(HostRefused):
        require_local("postgresql://u:p@db.example.com/x", "seed-snapshot")
    with pytest.raises(HostRefused):
        refuse_production("postgresql://u:p@prod-db.example.com/x", "init-db", "ALLOW_PRODUCTION_INIT")


def test_postgres_scratch_copy_flips_only_the_datasource():
    text = PRISMA_SCHEMA.read_text(encoding="utf-8") if PRISMA_SCHEMA.exists() else (
        'generator client {\n  provider = "prisma-client-js"\n  binaryTargets = ["native"]\n}\n'
        'datasource db {\n  provider = "sqlite"\n  url = env("DATABASE_URL")\n}\nmodel A {\n  id String @id\n}\n')
    out = to_postgres_schema(text)
    assert datasource_provider(text) == "sqlite" and datasource_provider(out) == "postgresql"
    assert re.search(r'provider\s*=\s*"prisma-client-js"', out) and "binaryTargets" not in out and 'env("DATABASE_URL")' in out
    assert sorted(parse_models(out)) == sorted(parse_models(text))
    assert out.count('"postgresql"') == 1 and "\r" not in out
    with pytest.raises(ValueError):
        to_postgres_schema("model A {\n id String @id\n}\n")


def test_prisma_parser_columns():
    m = parse_models('model A {\n  id String @id @default(cuid())\n  n Int?\n  d DateTime @updatedAt\n  b B @relation(fields:[x])\n  l B[]\n  @@index([n])\n}\n')
    cols = {c.name: c for c in m["A"].columns}
    assert list(cols) == ["id", "n", "d"] and cols["id"].has_default and cols["id"].is_id
    assert cols["n"].optional and cols["d"].is_updated_at


def test_seed_value_conversion():
    ts = "timestamp without time zone"
    assert ss.convert_value(1700000000000, ts) == datetime(2023, 11, 14, 22, 13, 20)
    assert ss.convert_value(1700000000, ts) == datetime(2023, 11, 14, 22, 13, 20)
    assert ss.convert_value("2024-05-01 10:00:00", ts) == datetime(2024, 5, 1, 10, 0, 0)
    assert ss.convert_value("2024-05-01T10:00:00+02:00", ts) == datetime(2024, 5, 1, 8, 0, 0)
    assert ss.convert_value("1700000000000", ts) == datetime(2023, 11, 14, 22, 13, 20)
    assert ss.convert_value(None, ts) is None
    assert ss.convert_value(1, "boolean") is True and ss.convert_value(0, "boolean") is False and ss.convert_value("false", "boolean") is False
    assert ss.convert_value("12", "integer") == 12 and ss.convert_value(3, "double precision") == 3.0
    assert ss.convert_value("x", "text") == "x" and ss.convert_value(5, "text") == "5"
    with pytest.raises(ValueError):
        ss.convert_value("maybe", "boolean")


def test_plan_columns_flags_missing_required_columns():
    T = ss.TargetColumn
    target = [T("id", "text", False, False), T("name", "text", False, False), T("updatedAt", "timestamp without time zone", False, False),
              T("verificationStatus", "text", False, True), T("nameZh", "text", True, False)]
    copy, problems = ss.plan_columns(["id", "name", "extraOldColumn"], target)
    assert [c.name for c in copy] == ["id", "name"] and problems == ["updatedAt"]


def test_snapshot_table_lists_never_mix_content_and_user_tables():
    assert not set(ss.CONTENT_TABLES) & set(ss.USER_TABLES)
    assert "Student" in ss.USER_TABLES and "Student" not in ss.CONTENT_TABLES and "Application" not in ss.CONTENT_TABLES
    assert len(ss.CONTENT_TABLES) == 10
    with pytest.raises(ValueError):
        ss.ident('x"; DROP TABLE "Student')


def test_dev_db_is_opened_read_only(tmp_path):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE University (id TEXT)")
    c.execute("INSERT INTO University VALUES ('a')")
    c.commit()
    c.close()
    ro = ss.open_dev_db(db)
    assert ro.execute("SELECT count(*) FROM University").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO University VALUES ('b')")
