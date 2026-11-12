"""LangGraph 분석 워크플로우.

    prepare_context ─▶ analyst ─(tool_calls)─▶ tools ─┐
                          ▲                           │
                          └───────────────────────────┘
                          │(도구 호출 없음 or 단계 초과)
                          ▼
                       write_report ─▶ judge ─(통과 or 재작성 한도)─▶ save_report ─▶ END
                          ▲              │
                          └──(불통과: 피드백 반영 재작성)

judge 노드는 analyst/judge.py 의 판정 모듈을 사용합니다 (기준: config/judge.yaml).
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import Settings, settings
from .connectors import BaseConnector, get_connector
from .judge import build_evidence, check_numbers, feedback_text, judge_report, load_judge_config
from .prompts import AGENDA_BLOCK, FOLLOWUP_BLOCK, REPORT_PROMPT, REVISION_PROMPT, SYSTEM_PROMPT
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
    judge: dict          # 최신 판정 결과
    revisions: int       # 판정 불통과로 재작성한 횟수


def _data_date(conn: BaseConnector) -> str:
    try:
        return str(conn.execute("SELECT MAX(date) AS d FROM g1_fact_sales_daily", 1).df.iloc[0, 0])
    except Exception:
        return "unknown"


def build_graph(llm: BaseChatModel, conn: BaseConnector, sdb: ServiceDB, cfg: Settings = settings,
                judge_llm: BaseChatModel | None = None, judge_cfg: dict | None = None):
    """judge_llm 을 따로 주면 그 모델로 판정합니다 (없으면 분석 모델 사용).
    판정은 cfg.judge_enabled 와 config/judge.yaml 의 enabled 가 모두 켜져 있을 때만 실행됩니다."""
    ws = Workspace()
    jcfg = judge_cfg or load_judge_config()
    use_judge = cfg.judge_enabled and jcfg.get("enabled", True)
    judge_llm = judge_llm or llm
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
        msgs = msgs + [HumanMessage(REPORT_PROMPT)]
        prev = state.get("judge")
        if prev and prev.get("passed") is False and state.get("report_md"):  # 판정 불통과 → 피드백 반영 재작성
            msgs += [AIMessage(state["report_md"]), HumanMessage(REVISION_PROMPT.format(feedback=feedback_text(prev)))]
        resp = llm.invoke(msgs)
        content = resp.content if isinstance(resp.content, str) else "".join(
            b.get("text", "") for b in resp.content if isinstance(b, dict))
        return {"report_md": content}

    def judge(state: AnalystState) -> AnalystState:
        nc_cfg = jcfg.get("number_check") or {}
        nc = (check_numbers(state["report_md"], ws.frames, ws.python_log, nc_cfg.get("tolerance", 0.01))
              if nc_cfg.get("enabled", True) else None)
        result = judge_report(judge_llm, state.get("question", ""), state["report_md"],
                              build_evidence(ws.sql_log, ws.frames, ws.python_log),
                              metrics=tool_by_name["lookup_metrics"].invoke({}), number_check=nc, cfg=jcfg)
        revisions = state.get("revisions", 0)
        result["revision"] = revisions
        history = [*(state.get("judge") or {}).get("history", []),
                   {k: result.get(k) for k in ("revision", "overall", "passed", "error")}]
        result["history"] = history
        return {"judge": result}

    def route_judge(state: AnalystState) -> str:
        j = state.get("judge") or {}
        if j.get("passed") is False and state.get("revisions", 0) < jcfg.get("max_revisions", 1):
            return "revise"
        return "save_report"

    def revise(state: AnalystState) -> AnalystState:
        return {"revisions": state.get("revisions", 0) + 1}

    def save_report(state: AnalystState) -> AnalystState:
        rid = sdb.save_report(state.get("question", ""), state.get("agenda_id"), state["report_md"], ws.sql_log,
                              charts=ws.charts, kind=state.get("kind") or "explore", judge=state.get("judge"))
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
    if use_judge:
        g.add_node("judge", judge)
        g.add_node("revise", revise)
        g.add_edge("write_report", "judge")
        g.add_conditional_edges("judge", route_judge, {"revise": "revise", "save_report": "save_report"})
        g.add_edge("revise", "write_report")
    else:
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
    from .llm import get_judge_llm, get_llm
    conn = get_connector(cfg.warehouse_connector, cfg.warehouse_dsn)
    sdb = ServiceDB(cfg.service_db_path)
    judge_llm = get_judge_llm(cfg) if (llm is None and cfg.judge_enabled) else None
    graph = build_graph(llm or get_llm(cfg), conn, sdb, cfg, judge_llm=judge_llm)
    final: dict = {}
    inputs = {"question": question, "agenda_id": agenda_id, "previous_report": previous_report, "kind": kind}
    limit = cfg.max_agent_steps * 2 + 10 + 3 * load_judge_config().get("max_revisions", 1)
    for update in graph.stream(inputs, {"recursion_limit": limit}, stream_mode="updates"):
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
            elif node == "judge":
                j = out["judge"]
                if j.get("passed") is None:
                    on_event(f'품질 판정 건너뜀 ({j.get("error", "")[:80]})')
                else:
                    on_event(f'품질 판정: {j["overall"]}점 · {"통과" if j["passed"] else "불통과"}')
            elif node == "revise":
                on_event("판정 의견을 반영해 리포트 재작성")
    ws = graph.workspace
    return {"report_id": final.get("report_id"), "report_md": final.get("report_md", ""),
            "sql_log": ws.sql_log, "charts": ws.charts, "steps": final.get("steps"), "judge": final.get("judge")}


def run_analysis(question: str, agenda_id: str | None = None, **kw) -> dict:
    return stream_analysis(question, agenda_id, **kw)
