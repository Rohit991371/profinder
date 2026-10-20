"""Postgres connections (psycopg 3). Import is lazy so modules that never touch a DB import without psycopg."""
from typing import Any


def connect(url: str, autocommit: bool = False) -> Any:
    import psycopg  # noqa: PLC0415 - lazy by design
    return psycopg.connect(url, autocommit=autocommit, connect_timeout=10)
