from __future__ import annotations

import sqlite3

import pandas as pd

from .base import BaseConnector, QueryResult


class SQLiteConnector(BaseConnector):
    dialect = "sqlite"

    def _connect(self) -> sqlite3.Connection:
        # mode=ro: DB 레벨에서도 쓰기 차단 (SQL 검사와 이중 안전장치)
        con = sqlite3.connect(f"file:{self.dsn}?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
        return con

    def list_tables(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            ).fetchall()
        return [{"name": n, "type": t} for n, t in rows]

    def describe_table(self, table: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT name, type FROM pragma_table_info(?)", (table,)).fetchall()
        return [{"column": n, "type": t or "ANY"} for n, t in rows]

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        with self._connect() as con:
            cur = con.execute(sql)
            cols = [c[0] for c in cur.description]
            rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        return QueryResult(pd.DataFrame(rows[:max_rows], columns=cols), truncated)
