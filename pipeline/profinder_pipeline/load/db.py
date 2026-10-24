"""Thin database wrapper so the loader logic can be tested without PostgreSQL (tests use sqlite3 with the same SQL).
SQL uses %s placeholders and quoted Prisma identifiers only."""
from typing import Any, Optional, Sequence


class PgDb:
    def __init__(self, conn: Any):
        self.conn = conn

    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def columns(self, table: str) -> set[str]:
        return {r["column_name"] for r in self.rows(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s", (table,))}

    def count(self, table: str) -> Optional[int]:
        if not self.columns(table):
            return None
        return int(self.rows(f'SELECT count(*) AS n FROM "{table}"')[0]["n"])

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()
