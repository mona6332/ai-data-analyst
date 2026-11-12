"""LangGraph 분석 워크플로우 (단일·멀티 에이전트 공통).

    ┌─▶ [분석 에이전트 A] ─┐      (multi: 정형·탐색 동시 실행 / single: 분석 에이전트 1개)
 START                    ├─▶ write_report ─▶ judge ─(통과 or 재작성 한도)─▶ save_report ─▶ END
    └─▶ [분석 에이전트 B] ─┘        ▲            │
                                    └─(불통과: 판정 의견 반영 재작성)

각 분석 에이전트 내부:  analyst ─(tool_calls)─▶ tools ─▶ analyst ... ─▶ 분석 메모
  - 에이전트마다 자기 Workspace(SQL 결과 r1, r2 ...)를 갖고, 끝나면 공용 Workspace로 합칩니다
    (이름 앞에 접두사: 정형 S_r1, 탐색 E_r1).
  - write_report = 리포트 작성 에이전트: 메모 + 실제 조회 결과만으로 리포트 작성 (새 조회 없음)
  - judge = 평가 에이전트: analyst/judge.py (기준 config/judge.yaml)

에이전트 역할·지시문·단계 수·모드 라우팅은 config/agents.yaml 에서 조정합니다.
"""
from __future__ import annotations

import queue
import threading
from typing import Annotated, Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import Settings, settings
from .connectors import BaseConnector, get_connector
from .definitions import load_agents
from .judge import build_evidence, check_numbers, feedback_text, judge_report, load_judge_config
from .prompts import (AGENDA_BLOCK, AGENT_ROLE_BLOCK, AUTO_AGENDA_BLOCK, FOLLOWUP_BLOCK, MEMO_PROMPT,
                      PERSPECTIVE_SECTION, REVISION_PROMPT, SYSTEM_PROMPT, WRITER_PROMPT, WRITER_SYSTEM)
from .service_db import ServiceDB
from .tools import Workspace, build_tools

# single 모드의 분석 에이전트: 아젠다를 고르면 아젠다 순서대로, 아니면 자유 분석 (역할 지시문 없음)
SINGLE_AGENT = {"id": "analyst", "name": "분석", "use_agenda": "selected", "instructions": "", "prefix": ""}
MODES = ("auto", "single", "multi")


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class AnalystState(TypedDict, total=False):
    question: str
    agenda_id: str | None
    previous_report: str | None   # 후속 질문일 때 직전 리포트
    kind: str                     # explore | auto | eval
    memos: Annotated[dict, _merge]        # 에이전트 id → {name, prefix, memo}
    agent_steps: Annotated[dict, _merge]  # 에이전트 id → 도구 호출 턴 수
    report_md: str
    report_id: str
    judge: dict          # 최신 판정 결과
    revisions: int       # 판정 불통과로 재작성한 횟수


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int


def _data_date(conn: BaseConnector) -> str:
    try:
        return str(conn.execute("SELECT MAX(date) AS d FROM g1_fact_sales_daily", 1).df.iloc[0, 0])
    except Exception:
        return "unknown"


def _text(resp) -> str:
    c = resp.content
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c if isinstance(b, dict))


def resolve_mode(mode: str | None, kind: str = "explore", agents_cfg: dict | None = None) -> str:
    """auto 모드를 요청 목적(kind)에 따라 single/multi 로 바꿉니다 (config/agents.yaml 의 routing)."""
    m = (mode or settings.agent_mode or "auto").lower()
    if m not in MODES:
        raise ValueError(f"지원하지 않는 모드: {m} (auto | single | multi)")
    if m == "auto":
        routing = (agents_cfg or load_agents())["routing"]
        m = routing.get({"auto": "auto_report"}.get(kind, kind), routing.get("explore", "multi"))
    return m


def build_graph(llm: BaseChatModel, conn: BaseConnector, sdb: ServiceDB, cfg: Settings = settings,
                judge_llm: BaseChatModel | None = None, judge_cfg: dict | None = None,
                mode: str = "single", agents_cfg: dict | None = None,
                emit: Callable[[str], None] | None = None):
    """mode: single | multi (auto 는 stream_analysis 에서 미리 바꿔서 넘김).
    judge_llm 을 따로 주면 그 모델로 판정합니다 (없으면 분석 모델 사용).
    emit(text): 분석 에이전트 내부 진행 상황(도구 호출)을 알리는 콜백."""
    acfg = agents_cfg or load_agents()
    jcfg = judge_cfg or load_judge_config()
    use_judge = cfg.judge_enabled and jcfg.get("enabled", True)
    judge_llm = judge_llm or llm
    emit = emit or (lambda _t: None)

    agents = [a for a in acfg["agents"] if a.get("enabled", True)] if mode == "multi" else []
    if not agents:
        agents = [SINGLE_AGENT]
    multi = len(agents) > 1

    ws = Workspace()  # 모든 에이전트의 결과를 합친 공용 Workspace (리포트 작성·평가·저장용)
    base = {t.name: t for t in build_tools(conn, sdb, cfg, Workspace())}
    schema, metrics = base["list_tables"].invoke({}), base["lookup_metrics"].invoke({})
    data_date = _data_date(conn)

    def system_prompt(agent: dict, state: AnalystState) -> tuple[str, str]:
        agenda = sdb.agenda(state.get("agenda_id"))
        ua = agent.get("use_agenda")
        block = ""
        if agenda and ua in (True, "selected"):
            steps = "\n".join(f'{i}. {s["name"]}: {s.get("guide", "")}' for i, s in enumerate(agenda["steps"], 1))
            block = AGENDA_BLOCK.format(title=agenda["title"], question=agenda["question"],
                                        metrics=", ".join(agenda.get("metrics", [])), steps=steps)
        elif ua is True:
            block = AUTO_AGENDA_BLOCK.format(agendas="\n".join(
                f'- {a["id"]} ({a["title"]}): {a["question"]} — ' + " → ".join(s["name"] for s in a["steps"])
                for a in sdb.agendas()))
        if state.get("previous_report"):
            block += "\n\n" + FOLLOWUP_BLOCK.format(previous=state["previous_report"][:4000])
        if agent.get("instructions"):
            block += "\n\n" + AGENT_ROLE_BLOCK.format(name=agent["name"], instructions=agent["instructions"].strip())
        system = SYSTEM_PROMPT.format(data_date=data_date, schema=schema, metrics=metrics, agenda_block=block)
        question = state.get("question") or (agenda["question"] if agenda else "")
        return system, question

    def make_agent_node(agent: dict):
        aws = Workspace()
        tools = build_tools(conn, sdb, cfg, aws)
        bound = llm.bind_tools(tools)
        max_steps = int(agent.get("max_steps") or cfg.max_agent_steps)
        tag = f'[{agent["name"]}] ' if multi else ""

        def call_llm(s: AgentState) -> AgentState:
            return {"messages": [bound.invoke(s["messages"])], "steps": s.get("steps", 0) + 1}

        def route(s: AgentState) -> str:
            return "tools" if getattr(s["messages"][-1], "tool_calls", None) and s.get("steps", 0) < max_steps else END

        sg = StateGraph(AgentState)
        sg.add_node("analyst", call_llm)
        sg.add_node("tools", ToolNode(tools))
        sg.add_edge(START, "analyst")
        sg.add_conditional_edges("analyst", route, {"tools": "tools", END: END})
        sg.add_edge("tools", "analyst")
        sub = sg.compile()

        def node(state: AnalystState) -> AnalystState:
            system, question = system_prompt(agent, state)
            final: dict = {}
            for st in sub.stream({"messages": [SystemMessage(system), HumanMessage(question)], "steps": 0},
                                 {"recursion_limit": max_steps * 2 + 5}, stream_mode="values"):
                final = st
                last = st["messages"][-1]
                if isinstance(last, AIMessage):
                    for tc in last.tool_calls or []:
                        a = tc["args"]
                        emit(f'{tag}{TOOL_LABEL.get(tc["name"], tc["name"])}: {a.get("purpose") or a.get("title") or ""}')
            msgs = list(final.get("messages", []))
            if msgs and getattr(msgs[-1], "tool_calls", None):  # 단계 초과로 미처리 tool_call 이 남은 경우
                msgs = msgs[:-1]
            memo = _text(msgs[-1]) if msgs and isinstance(msgs[-1], AIMessage) else ""
            if not memo.strip():  # 마지막 응답이 비어 있으면 메모를 따로 요청
                memo = _text(llm.invoke(msgs + [HumanMessage(MEMO_PROMPT)]))
            # 공용 Workspace 로 합치기 (이름 앞에 접두사)
            p = agent.get("prefix", "")
            for name, df in aws.frames.items():
                ws.frames[p + name] = df
            for s in aws.sql_log:
                e = {**s, "agent": agent["name"]}
                if "name" in e:
                    e["name"] = p + e["name"]
                ws.sql_log.append(e)
            ws.python_log.extend({**x, "agent": agent["name"]} for x in aws.python_log)
            ws.charts.extend({**c, "title": tag + c["title"], "agent": agent["name"]} for c in aws.charts)
            emit(f"{tag}분석 메모 완료")
            return {"memos": {agent["id"]: {"name": agent["name"], "prefix": p, "memo": memo}},
                    "agent_steps": {agent["id"]: final.get("steps", 0)}}

        return node

    def write_report(state: AnalystState) -> AnalystState:
        writer = acfg["writer"]
        memos = state.get("memos") or {}
        memo_txt = "\n\n".join(
            f'### {m["name"]}' + (f' (결과 이름 앞에 {m["prefix"]})' if m["prefix"] else "") + f'\n{m["memo"]}'
            for m in (memos.get(a["id"]) for a in agents) if m)
        system = WRITER_SYSTEM.format(instructions=(writer.get("instructions") or "").strip(), data_date=data_date,
                                      metrics=metrics,
                                      evidence=build_evidence(ws.sql_log, ws.frames, ws.python_log,
                                                              max_chars=30000 if multi else 15000))
        human = WRITER_PROMPT.format(question=state.get("question") or "(아젠다 정기 분석)", memos=memo_txt,
                                     cross_mark=", 여러 에이전트가 같은 결론이면 '(교차 확인)' 표시" if multi else "",
                                     perspective_section=PERSPECTIVE_SECTION if multi else "")
        msgs = [SystemMessage(system), HumanMessage(human)]
        prev = state.get("judge")
        if prev and prev.get("passed") is False and state.get("report_md"):  # 판정 불통과 → 피드백 반영 재작성
            msgs += [AIMessage(state["report_md"]), HumanMessage(REVISION_PROMPT.format(feedback=feedback_text(prev)))]
        return {"report_md": _text(llm.invoke(msgs))}

    def judge(state: AnalystState) -> AnalystState:
        nc_cfg = jcfg.get("number_check") or {}
        nc = (check_numbers(state["report_md"], ws.frames, ws.python_log, nc_cfg.get("tolerance", 0.01))
              if nc_cfg.get("enabled", True) else None)
        result = judge_report(judge_llm, state.get("question", ""), state["report_md"],
                              build_evidence(ws.sql_log, ws.frames, ws.python_log,
                                             max_chars=30000 if multi else 15000),
                              metrics=metrics, number_check=nc, cfg=jcfg)
        result["revision"] = state.get("revisions", 0)
        result["history"] = [*(state.get("judge") or {}).get("history", []),
                             {k: result.get(k) for k in ("revision", "overall", "passed", "error")}]
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
    node_names = []
    for a in agents:
        name = f'agent_{a["id"]}'
        g.add_node(name, make_agent_node(a))
        g.add_edge(START, name)
        node_names.append(name)
    g.add_node("write_report", write_report)
    g.add_node("save_report", save_report)
    g.add_edge(node_names if len(node_names) > 1 else node_names[0], "write_report")
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
    graph.mode = "multi" if multi else "single"
    graph.agent_names = [a["name"] for a in agents]
    return graph


TOOL_LABEL = {"run_sql": "SQL 조회", "python_analysis": "Python 분석", "describe_table": "테이블 확인",
              "list_tables": "테이블 목록", "lookup_metrics": "지표 사전", "make_chart": "차트 생성"}


def stream_analysis(question: str, agenda_id: str | None = None, previous_report: str | None = None,
                    kind: str = "explore", llm: BaseChatModel | None = None, cfg: Settings = settings,
                    on_event=None, mode: str | None = None, judge_llm: BaseChatModel | None = None) -> dict:
    """분석 실행. on_event(text) 로 진행 상황을 전달합니다 (화면 표시용, 호출한 스레드에서 실행됨).
    mode: auto | single | multi (없으면 .env 의 AGENT_MODE)."""
    from .llm import get_judge_llm, get_llm
    acfg = load_agents()
    m = resolve_mode(mode or cfg.agent_mode, kind, acfg)
    conn = get_connector(cfg.warehouse_connector, cfg.warehouse_dsn)
    sdb = ServiceDB(cfg.service_db_path)
    if judge_llm is None and llm is None and cfg.judge_enabled:
        judge_llm = get_judge_llm(cfg)
    events: queue.Queue = queue.Queue()
    graph = build_graph(llm or get_llm(cfg), conn, sdb, cfg, judge_llm=judge_llm, mode=m, agents_cfg=acfg,
                        emit=lambda t: events.put(("event", t)))
    inputs = {"question": question, "agenda_id": agenda_id, "previous_report": previous_report, "kind": kind}
    limit = 20 + 3 * load_judge_config().get("max_revisions", 1)

    def worker():  # 에이전트들이 병렬로 도는 동안 진행 상황을 메인 스레드로 전달 (Streamlit은 메인 스레드에서만 그림)
        final: dict = {}
        try:
            for update in graph.stream(inputs, {"recursion_limit": limit}, stream_mode="updates"):
                for node, out in update.items():
                    for k, v in (out or {}).items():  # memos·agent_steps 는 병렬 에이전트 결과를 합침 (덮어쓰기 X)
                        final[k] = _merge(final.get(k), v) if k in ("memos", "agent_steps") else v
                    if node == "write_report":
                        events.put(("event", "리포트 작성 에이전트: 작성 완료"))
                    elif node == "judge":
                        j = out["judge"]
                        events.put(("event", f'평가 에이전트: 판정 건너뜀 ({j.get("error", "")[:80]})'
                                    if j.get("passed") is None else
                                    f'평가 에이전트: {j["overall"]}점 · {"통과" if j["passed"] else "불통과"}'))
                    elif node == "revise":
                        events.put(("event", "평가 의견을 반영해 리포트 재작성"))
            events.put(("done", final))
        except BaseException as e:  # noqa: BLE001 — 메인 스레드에서 다시 발생시킴
            events.put(("error", e))

    if on_event:
        on_event(f'실행 모드: {"멀티 에이전트" if graph.mode == "multi" else "단일 에이전트"} ({", ".join(graph.agent_names)})')
    threading.Thread(target=worker, daemon=True).start()
    while True:
        typ, payload = events.get()
        if typ == "event":
            if on_event:
                on_event(payload)
        elif typ == "done":
            final = payload
            break
        else:
            raise payload
    ws = graph.workspace
    return {"report_id": final.get("report_id"), "report_md": final.get("report_md", ""),
            "sql_log": ws.sql_log, "charts": ws.charts, "steps": sum((final.get("agent_steps") or {}).values()),
            "judge": final.get("judge"), "mode": graph.mode, "agents": graph.agent_names,
            "memos": final.get("memos") or {}}


def run_analysis(question: str, agenda_id: str | None = None, **kw) -> dict:
    return stream_analysis(question, agenda_id, **kw)
