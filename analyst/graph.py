"""LangGraph 분석 워크플로우.

    prepare_context ─▶ analyst ─(tool_calls)─▶ tools ─┐
                          ▲                           │
                          └───────────────────────────┘
                          │(도구 호출 없음 or 단계 초과)
                          ▼
                       write_report ─▶ save_report ─▶ END
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import Settings, settings
from .connectors import BaseConnector, get_connector
from .prompts import AGENDA_BLOCK, FOLLOWUP_BLOCK, REPORT_PROMPT, SYSTEM_PROMPT
from .service_db import ServiceDB
from .tools import Workspace, build_tools


class AnalystState(TypedDict, total=False):
    question: str
    agenda_id: str | None
    previous_report: str | None   # 후속 질문일 때 직전 리포트
    kind: str                     # explore | auto
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    report_md: str
    report_id: str


def _data_date(conn: BaseConnector) -> str:
    try:
        return str(conn.execute("SELECT MAX(date) AS d FROM g1_fact_sales_daily", 1).df.iloc[0, 0])
    except Exception:
        return "unknown"


def build_graph(llm: BaseChatModel, conn: BaseConnector, sdb: ServiceDB, cfg: Settings = settings):
    ws = Workspace()
    tools = build_tools(conn, sdb, cfg, ws)
    tool_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)

    def prepare_context(state: AnalystState) -> AnalystState:
        agenda = sdb.agenda(state.get("agenda_id"))
        agenda_block = ""
        if agenda:
            steps = "\n".join(f'{i}. {s["name"]}: {s.get("guide", "")}' for i, s in enumerate(agenda["steps"], 1))
            agenda_block = AGENDA_BLOCK.format(title=agenda["title"], question=agenda["question"],
                                               metrics=", ".join(agenda.get("metrics", [])), steps=steps)
        if state.get("previous_report"):
            agenda_block += "\n\n" + FOLLOWUP_BLOCK.format(previous=state["previous_report"][:4000])
        system = SYSTEM_PROMPT.format(
            data_date=_data_date(conn),
            schema=tool_by_name["list_tables"].invoke({}),
            metrics=tool_by_name["lookup_metrics"].invoke({}),
            agenda_block=agenda_block,
        )
        question = state.get("question") or (agenda["question"] if agenda else "")
        return {"messages": [SystemMessage(system), HumanMessage(question)], "steps": 0}

    def analyst(state: AnalystState) -> AnalystState:
        resp = llm_with_tools.invoke(state["messages"])
        return {"messages": [resp], "steps": state.get("steps", 0) + 1}

    def route(state: AnalystState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None) and state.get("steps", 0) < cfg.max_agent_steps:
            return "tools"
        return "write_report"

    def write_report(state: AnalystState) -> AnalystState:
        msgs = list(state["messages"])
        # 마지막 AI 메시지가 미처리 tool_call을 갖고 끝났으면(단계 초과) 제거
        if getattr(msgs[-1], "tool_calls", None):
            msgs = msgs[:-1]
        resp = llm.invoke(msgs + [HumanMessage(REPORT_PROMPT)])
        content = resp.content if isinstance(resp.content, str) else "".join(
            b.get("text", "") for b in resp.content if isinstance(b, dict))
        return {"report_md": content}

    def save_report(state: AnalystState) -> AnalystState:
        rid = sdb.save_report(state.get("question", ""), state.get("agenda_id"), state["report_md"], ws.sql_log,
                              charts=ws.charts, kind=state.get("kind") or "explore")
        return {"report_id": rid}

    g = StateGraph(AnalystState)
    g.add_node("prepare_context", prepare_context)
    g.add_node("analyst", analyst)
    g.add_node("tools", ToolNode(tools))
    g.add_node("write_report", write_report)
    g.add_node("save_report", save_report)
    g.add_edge(START, "prepare_context")
    g.add_edge("prepare_context", "analyst")
    g.add_conditional_edges("analyst", route, {"tools": "tools", "write_report": "write_report"})
    g.add_edge("tools", "analyst")
    g.add_edge("write_report", "save_report")
    g.add_edge("save_report", END)
    graph = g.compile()
    graph.workspace = ws  # 실행 후 SQL 로그/DataFrame 확인용
    return graph


TOOL_LABEL = {"run_sql": "SQL 조회", "python_analysis": "Python 분석", "describe_table": "테이블 확인",
              "list_tables": "테이블 목록", "lookup_metrics": "지표 사전", "make_chart": "차트 생성"}


def stream_analysis(question: str, agenda_id: str | None = None, previous_report: str | None = None,
                    kind: str = "explore", llm: BaseChatModel | None = None, cfg: Settings = settings,
                    on_event=None) -> dict:
    """분석 실행. on_event(text) 로 진행 상황을 전달합니다 (화면 표시용)."""
    from .llm import get_llm
    conn = get_connector(cfg.warehouse_connector, cfg.warehouse_dsn)
    sdb = ServiceDB(cfg.service_db_path)
    graph = build_graph(llm or get_llm(cfg), conn, sdb, cfg)
    final: dict = {}
    inputs = {"question": question, "agenda_id": agenda_id, "previous_report": previous_report, "kind": kind}
    for update in graph.stream(inputs, {"recursion_limit": cfg.max_agent_steps * 2 + 10}, stream_mode="updates"):
        for node, out in update.items():
            final.update(out or {})
            if not on_event:
                continue
            if node == "analyst":
                for tc in getattr(out["messages"][-1], "tool_calls", None) or []:
                    a = tc["args"]
                    on_event(f'{TOOL_LABEL.get(tc["name"], tc["name"])}: {a.get("purpose") or a.get("title") or ""}')
            elif node == "write_report":
                on_event("리포트 작성 완료")
    ws = graph.workspace
    return {"report_id": final.get("report_id"), "report_md": final.get("report_md", ""),
            "sql_log": ws.sql_log, "charts": ws.charts, "steps": final.get("steps")}


def run_analysis(question: str, agenda_id: str | None = None, **kw) -> dict:
    return stream_analysis(question, agenda_id, **kw)
