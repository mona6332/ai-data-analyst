"""에이전트가 호출하는 도구 (Tool Calling).

한 번의 분석 실행(run)마다 Workspace를 만들고, SQL 결과 DataFrame을 r1, r2 ... 로 저장해
Python 분석 도구에서 이어 쓸 수 있게 합니다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd
from langchain_core.tools import tool

from .config import Settings
from .connectors import BaseConnector
from .sandbox import run_python
from .service_db import ServiceDB
from .sql_guard import UnsafeSQLError, validate_sql


@dataclass
class Workspace:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    sql_log: list[dict] = field(default_factory=list)
    python_log: list[dict] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)


def _preview(df: pd.DataFrame, n: int = 30) -> str:
    return df.head(n).to_markdown(index=False) if len(df) else "(0 rows)"


def build_tools(conn: BaseConnector, sdb: ServiceDB, cfg: Settings, ws: Workspace):
    prefixes = cfg.allowed_table_prefixes

    def _allowed(name: str) -> bool:
        return name.lower().startswith(tuple(prefixes))

    @tool
    def list_tables() -> str:
        """분석에 사용할 수 있는 테이블/뷰 목록과 컬럼을 반환합니다 (GOLD·G1·도메인)."""
        out = []
        for t in conn.list_tables():
            if _allowed(t["name"]):
                cols = ", ".join(f'{c["column"]}' for c in conn.describe_table(t["name"]))
                out.append(f'- {t["name"]} ({t["type"]}): {cols}')
        return "\n".join(out)

    @tool
    def describe_table(table: str) -> str:
        """테이블의 컬럼 타입과 샘플 3행을 반환합니다."""
        if not _allowed(table):
            return f"ERROR: {table} 은(는) 조회 권한이 없습니다."
        cols = conn.describe_table(table)
        sample = conn.execute(f'SELECT * FROM "{table}" LIMIT 3', 3).df
        return json.dumps(cols, ensure_ascii=False) + "\n샘플:\n" + _preview(sample)

    @tool
    def lookup_metrics() -> str:
        """지표 사전(표준 지표 정의와 계산식)을 반환합니다. 지표 계산 시 이 정의를 따르세요."""
        return "\n".join(f'- {m["metric_key"]} ({m["name_ko"]}, {"높을수록 좋음" if m["direction"] == "up" else "낮을수록 좋음"}): '
                         f'{m["definition"]} | {m["formula_sql"]} @ {m["source_table"]}' for m in sdb.metrics())

    @tool
    def run_sql(sql: str, purpose: str) -> str:
        """읽기 전용 SELECT 쿼리를 실행합니다. 결과는 r1, r2... 이름의 DataFrame으로 저장되어 run_python에서 쓸 수 있습니다.

        Args:
            sql: 실행할 SELECT 문 (SQLite 문법). 허용 테이블: gold_*, g1_*, domain_*
            purpose: 이 쿼리로 확인하려는 것 (한 줄)
        """
        try:
            safe = validate_sql(sql, conn.dialect, prefixes, cfg.max_rows)
            res = conn.execute(safe, cfg.max_rows)
        except UnsafeSQLError as e:
            ws.sql_log.append({"sql": sql, "purpose": purpose, "error": str(e)})
            return f"BLOCKED: {e}"
        except Exception as e:  # DB 오류는 에이전트가 스스로 고치도록 반환
            ws.sql_log.append({"sql": sql, "purpose": purpose, "error": str(e)})
            return f"SQL ERROR: {e}"
        name = f"r{len(ws.frames) + 1}"
        ws.frames[name] = res.df
        ws.sql_log.append({"name": name, "sql": safe, "purpose": purpose, "rows": len(res.df)})
        note = f" (최대 {cfg.max_rows}행으로 잘림)" if res.truncated else ""
        return f"[{name}] {len(res.df)} rows{note}\n{_preview(res.df)}"

    @tool
    def python_analysis(code: str) -> str:
        """pandas/numpy로 추가 분석을 수행합니다. run_sql 결과는 r1, r2... 변수로 사용할 수 있습니다.
        import 없이 pd, np 를 바로 쓰고, 결과는 print()로 출력하세요. 예: print(r1.pct_change().tail())
        """
        out = run_python(code, ws.frames)
        ws.python_log.append({"code": code, "output": out[:2000]})
        return out

    @tool
    def make_chart(result: str, x: str, y: list[str], kind: str, title: str, color: str | None = None) -> str:
        """리포트에 첨부할 차트를 만듭니다. 핵심 발견을 보여주는 차트 1~3개만 만드세요.

        Args:
            result: 데이터로 쓸 결과 이름 (r1, r2 ...)
            x: x축 컬럼 (날짜/주차/카테고리 등)
            y: y축 컬럼 목록 (숫자)
            kind: line | bar
            title: 차트 제목 (한국어, 무엇을 보여주는지)
            color: (선택) 색으로 구분할 컬럼. 지정하면 y는 1개만
        """
        df = ws.frames.get(result)
        if df is None:
            return f"ERROR: {result} 결과가 없습니다. 사용 가능: {list(ws.frames)}"
        missing = [c for c in [x, *y, *([color] if color else [])] if c not in df.columns]
        if missing:
            return f"ERROR: 없는 컬럼 {missing}. 컬럼: {list(df.columns)}"
        if kind not in ("line", "bar"):
            return "ERROR: kind 는 line 또는 bar"
        cols = [x, *y, *([color] if color else [])]
        ws.charts.append({"title": title, "kind": kind, "x": x, "y": y, "color": color,
                          "data": df[cols].head(500).to_dict(orient="records")})
        return f"차트 {len(ws.charts)} 생성: {title}"

    return [list_tables, describe_table, lookup_metrics, run_sql, python_analysis, make_chart]
