"""Streamlit 공용 컴포넌트 (차트·리포트 렌더링, 분석 실행)."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from .config import settings
from .connectors import get_connector
from .service_db import ServiceDB


@st.cache_resource
def get_conn():
    return get_connector(settings.warehouse_connector, settings.warehouse_dsn)


def get_sdb() -> ServiceDB:
    return ServiceDB(settings.service_db_path)  # 매번 YAML과 동기화 (정의 수정 즉시 반영)


def has_llm_key() -> bool:
    import os
    return bool(os.getenv("OPENAI_API_KEY") if settings.llm_provider == "openai" else os.getenv("ANTHROPIC_API_KEY"))


def key_warning():
    if not has_llm_key():
        name = "OPENAI_API_KEY" if settings.llm_provider == "openai" else "ANTHROPIC_API_KEY"
        st.warning(f"LLM API 키가 없습니다. `.env` 파일에 `{name}=...` 를 넣고 앱을 다시 실행하세요. "
                   "(대시보드·지표 관리는 키 없이도 동작합니다)")


def render_chart(spec: dict):
    df = pd.DataFrame(spec["data"])
    if df.empty:
        return
    x, ys, color = spec["x"], spec["y"], spec.get("color")
    if color:
        long, ycol = df, ys[0]
    else:
        long = df.melt(id_vars=[x], value_vars=ys, var_name="지표", value_name="값")
        ycol, color = "값", "지표" if len(ys) > 1 else None
    mark = alt.Chart(long).mark_line(point=True) if spec["kind"] == "line" else alt.Chart(long).mark_bar()
    enc = {"x": alt.X(f"{x}:N" if spec["kind"] == "bar" else x, title=x, sort=None),
           "y": alt.Y(f"{ycol}:Q", title=ycol), "tooltip": list(long.columns)}
    if color:
        enc["color"] = alt.Color(f"{color}:N")
        if spec["kind"] == "bar":
            enc["xOffset"] = alt.XOffset(f"{color}:N")
    st.altair_chart(mark.encode(**enc).properties(title=spec["title"], height=280), width="stretch")


def render_report(report_md: str, charts: list | None = None, sql_log: list | None = None, key: str = ""):
    st.markdown(report_md)
    for c in charts or []:
        render_chart(c)
    if sql_log:
        with st.expander(f"실행한 쿼리 {len(sql_log)}개"):
            for s in sql_log:
                st.caption(f'[{s.get("name", "오류")}] {s["purpose"]}')
                st.code(s["sql"], language="sql")
                if "error" in s:
                    st.error(s["error"])
    if report_md:
        st.download_button("리포트 다운로드 (.md)", report_md, file_name=f"report_{key or 'analysis'}.md",
                           key=f"dl_{key}")


def run_with_status(question: str, agenda_id: str | None, previous_report: str | None = None,
                    kind: str = "explore") -> dict | None:
    from .graph import stream_analysis
    try:
        with st.status("분석 중...", expanded=True) as status:
            res = stream_analysis(question, agenda_id, previous_report=previous_report, kind=kind,
                                  on_event=lambda t: st.write("· " + t))
            status.update(label=f'분석 완료 (쿼리 {len(res["sql_log"])}개, 차트 {len(res["charts"])}개)',
                          state="complete", expanded=False)
        return res
    except Exception as e:
        st.error(f"분석 실패: {e}")
        return None
