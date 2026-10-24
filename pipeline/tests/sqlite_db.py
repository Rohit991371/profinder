"""sqlite3 stand-in for PostgreSQL in the loader tests: same SQL (%s placeholders, quoted identifiers), Prisma-like table shapes."""
import sqlite3
from datetime import datetime
from typing import Any, Sequence

DDL = [
    'CREATE TABLE "University" (id TEXT PRIMARY KEY, name TEXT, "nameZh" TEXT, country TEXT, province TEXT, city TEXT, "officialUrl" TEXT, "sourceUrl" TEXT, "agencyNumber" TEXT, "openAlexId" TEXT, "dataStatus" TEXT, "dataSource" TEXT, "verifiedAt" TEXT, "verificationStatus" TEXT, "lastCheckedAt" TEXT, confidence REAL, "createdAt" TEXT, "updatedAt" TEXT)',
    'CREATE TABLE "Professor" (id TEXT PRIMARY KEY, name TEXT, "universityId" TEXT, "firstName" TEXT, "lastName" TEXT, "nameZh" TEXT, position TEXT, school TEXT, department TEXT, country TEXT, email TEXT, "profileUrl" TEXT, "personalWebsite" TEXT, "researchInterests" TEXT, "researchKeywords" TEXT, lab TEXT, "profileIsPersonal" INTEGER DEFAULT 0, "dataStatus" TEXT, "dataSource" TEXT, "lastSyncedAt" TEXT, "verifiedAt" TEXT, "verificationStatus" TEXT, "lastCheckedAt" TEXT, "lastVerifiedAt" TEXT, confidence REAL, "createdAt" TEXT, "updatedAt" TEXT)',
    'CREATE TABLE "Program" (id TEXT PRIMARY KEY, "universityId" TEXT, degree TEXT, major TEXT, "officialName" TEXT, "programUrl" TEXT, "sourceUrl" TEXT, deadline TEXT, "verificationStatus" TEXT, "lastCheckedAt" TEXT, "lastVerifiedAt" TEXT, confidence REAL, "createdAt" TEXT, "updatedAt" TEXT)',
    'CREATE TABLE "Scholarship" (id TEXT PRIMARY KEY, "universityId" TEXT, name TEXT, type TEXT, deadline TEXT, "officialUrl" TEXT, "dataStatus" TEXT, "verificationStatus" TEXT, "lastCheckedAt" TEXT, "lastVerifiedAt" TEXT, confidence REAL, "createdAt" TEXT)',
    'CREATE TABLE "ResearchArea" (id TEXT PRIMARY KEY, name TEXT UNIQUE)',
    'CREATE TABLE "ProfessorResearchArea" ("professorId" TEXT, "researchAreaId" TEXT, PRIMARY KEY ("professorId", "researchAreaId"))',
    'CREATE TABLE "ProfessorSource" (id TEXT PRIMARY KEY, "professorId" TEXT, source TEXT, "externalId" TEXT, "sourceUrl" TEXT, "fetchedAt" TEXT)',
    'CREATE TABLE "SourceRecord" (id TEXT PRIMARY KEY, "entityType" TEXT, "entityId" TEXT, url TEXT, "sourceType" TEXT, "verificationStatus" TEXT, "checkedAt" TEXT, "verifiedAt" TEXT, confidence REAL, "httpStatus" INTEGER, "contentHash" TEXT, "createdAt" TEXT, "updatedAt" TEXT)',
    'CREATE TABLE "ChangeLog" (id TEXT PRIMARY KEY, "entityType" TEXT, "entityId" TEXT, "changeType" TEXT, "beforeJson" TEXT, "afterJson" TEXT, "sourceUrl" TEXT, "detectedAt" TEXT, "reviewedAt" TEXT, "reviewedBy" TEXT)',
    'CREATE TABLE "Student" (id TEXT PRIMARY KEY, email TEXT)',
    'CREATE TABLE "Application" (id TEXT PRIMARY KEY, "professorId" TEXT)',
    'CREATE TABLE "SavedProfessor" (id TEXT PRIMARY KEY, "professorId" TEXT)',
]


class SqliteDb:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        for ddl in DDL:
            self.conn.execute(ddl)

    @staticmethod
    def _params(params: Sequence[Any]) -> list[Any]:
        return [p.isoformat() if isinstance(p, datetime) else p for p in params]

    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute(sql.replace("%s", "?"), self._params(params)).fetchall():
            d = dict(r)
            for k, v in d.items():
                if k.endswith("At") and isinstance(v, str):
                    d[k] = datetime.fromisoformat(v)
            out.append(d)
        return out

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self.conn.execute(sql.replace("%s", "?"), self._params(params)).rowcount

    def columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')}

    def count(self, table: str):
        return self.conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] if self.columns(table) else None

    def insert(self, table: str, **vals: Any) -> None:
        ks = list(vals)
        self.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + k + chr(34) for k in ks)}) VALUES ({", ".join(["%s"] * len(ks))})', [vals[k] for k in ks])

    def dump(self, table: str, skip: Sequence[str] = ()) -> list[tuple]:
        rows = self.rows(f'SELECT * FROM "{table}" ORDER BY 1')
        return [tuple((k, str(v)) for k, v in sorted(r.items()) if k not in skip) for r in rows]
