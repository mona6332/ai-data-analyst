"""LLM 없이 그래프 흐름 검증: 정해진 tool_call 을 내는 가짜 모델 사용."""
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from analyst.config import Settings
from analyst.connectors import get_connector
from analyst.graph import build_graph
from analyst.service_db import ServiceDB


class FakeToolLLM(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kw):
        return self


def test_graph_end_to_end(tmp_path):
    llm = FakeToolLLM(responses=[
        AIMessage(content="", tool_calls=[{"id": "1", "name": "run_sql", "args": {
            "sql": "SELECT channel, SUM(attributed_revenue)*1.0/SUM(spend) AS roas FROM g1_fact_marketing_daily "
                   "WHERE date >= '2026-10-07' GROUP BY 1", "purpose": "최근 3주 채널별 ROAS"}}]),
        AIMessage(content="", tool_calls=[{"id": "2", "name": "python_analysis",
                                           "args": {"code": "print(r1.sort_values('roas').head(1))"}}]),
        AIMessage(content="", tool_calls=[{"id": "3", "name": "make_chart", "args": {
            "result": "r1", "x": "channel", "y": ["roas"], "kind": "bar", "title": "채널별 ROAS"}}]),
        AIMessage(content="분석 메모: META ROAS 최저"),
        AIMessage(content="# 리포트\n**요약** — META 효율 하락"),
    ])
    cfg = Settings()
    sdb = ServiceDB(str(tmp_path / "service.db"))
    graph = build_graph(llm, get_connector("sqlite", cfg.warehouse_dsn), sdb, cfg)
    out = graph.invoke({"question": "광고비는 늘었는데 ROAS가 떨어졌다", "agenda_id": "ad_efficiency"})
    assert out["report_md"].startswith("# 리포트")
    assert graph.workspace.sql_log[0]["rows"] > 0
    assert sdb.reports()[0]["report_id"] == out["report_id"]
    saved = sdb.report(out["report_id"])
    assert saved["charts"][0]["title"] == "채널별 ROAS" and saved["sql_log"]
