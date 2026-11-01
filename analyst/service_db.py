"""서비스 DB: 화면이 공유하는 정의와 기록 (지표 사전, 아젠다, 분석 리포트).

정의의 원본은 config/*.yaml 이고, 서비스 DB는 시작 시 YAML 내용으로 동기화됩니다.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

from .definitions import load_agendas, load_metrics


class ServiceDB:
    def __init__(self, path: str):
        self.path = path
        self._init()
        self.sync_definitions()

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS metric_dictionary(
              metric_key TEXT PRIMARY KEY, name_ko TEXT, group_name TEXT, definition TEXT, formula_sql TEXT,
              source_table TEXT, direction TEXT, alert_pct REAL, format TEXT);
            CREATE TABLE IF NOT EXISTS agendas(agenda_id TEXT PRIMARY KEY, title TEXT, question TEXT, spec_json TEXT);
            CREATE TABLE IF NOT EXISTS analysis_reports(
              report_id TEXT PRIMARY KEY, created_at TEXT, kind TEXT, agenda_id TEXT, question TEXT,
              report_md TEXT, sql_log_json TEXT, charts_json TEXT, status TEXT);
            """)

    def sync_definitions(self):
        metrics, agendas = load_metrics(), load_agendas()
        with self._con() as con:
            con.execute("DELETE FROM metric_dictionary")
            con.executemany("INSERT INTO metric_dictionary VALUES (?,?,?,?,?,?,?,?,?)", [
                (m["key"], m["name"], m["group"], m["definition"], m["formula"], m["source"], m["direction"],
                 m["alert_pct"], m["format"]) for m in metrics])
            con.execute("DELETE FROM agendas")
            con.executemany("INSERT INTO agendas VALUES (?,?,?,?)", [
                (a["id"], a["title"], a["question"], json.dumps(a, ensure_ascii=False)) for a in agendas])

    # ---------- 정의
    def metrics(self) -> list[dict]:
        with self._con() as con:
            return [dict(r) for r in con.execute("SELECT * FROM metric_dictionary")]

    def metric(self, key: str) -> dict | None:
        return next((m for m in self.metrics() if m["metric_key"] == key), None)

    def agendas(self) -> list[dict]:
        with self._con() as con:
            return [json.loads(r[0]) for r in con.execute("SELECT spec_json FROM agendas")]

    def agenda(self, agenda_id: str | None) -> dict | None:
        return next((a for a in self.agendas() if a["id"] == agenda_id), None) if agenda_id else None

    # ---------- 리포트
    def save_report(self, question: str, agenda_id: str | None, report_md: str, sql_log: list,
                    charts: list | None = None, kind: str = "explore", status: str = "done") -> str:
        rid = uuid.uuid4().hex[:12]
        with self._con() as con:
            con.execute("INSERT INTO analysis_reports VALUES (?,?,?,?,?,?,?,?,?)",
                        (rid, datetime.now().isoformat(timespec="seconds"), kind, agenda_id, question, report_md,
                         json.dumps(sql_log, ensure_ascii=False, default=str),
                         json.dumps(charts or [], ensure_ascii=False, default=str), status))
        return rid

    def reports(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        q = "SELECT report_id, created_at, kind, agenda_id, question, report_md FROM analysis_reports"
        args: tuple = ()
        if kind:
            q += " WHERE kind = ?"
            args = (kind,)
        with self._con() as con:
            return [dict(r) for r in con.execute(q + " ORDER BY created_at DESC LIMIT ?", (*args, limit))]

    def report(self, report_id: str) -> dict | None:
        with self._con() as con:
            r = con.execute("SELECT * FROM analysis_reports WHERE report_id = ?", (report_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["sql_log"] = json.loads(d.pop("sql_log_json") or "[]")
        d["charts"] = json.loads(d.pop("charts_json") or "[]")
        return d

    def delete_report(self, report_id: str) -> None:
        with self._con() as con:
            con.execute("DELETE FROM analysis_reports WHERE report_id = ?", (report_id,))
