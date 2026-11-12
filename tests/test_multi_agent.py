"""멀티 에이전트(정형·탐색 → 리포트 작성 → 평가) 흐름 테스트 — 역할별로 다르게 답하는 가짜 모델 사용."""
import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from analyst.config import Settings
from analyst.connectors import get_connector
from analyst.definitions import load_agents
from analyst.graph import build_graph, resolve_mode, stream_analysis
from analyst.judge import load_judge_config
from analyst.service_db import ServiceDB

SQL = {"정형 분석": "SELECT channel, SUM(spend) AS spend FROM g1_fact_marketing_daily GROUP BY 1",
       "자율 탐색": "SELECT category, SUM(return_units) AS ret FROM g1_fact_sales_daily GROUP BY 1"}


class RoleFakeLLM(BaseChatModel):
    """시스템 프롬프트로 역할을 구분: 분석 에이전트는 SQL 1번 → 메모, 작성 에이전트는 리포트, 판사는 JSON."""
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "role-fake"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        system = messages[0].content if messages else ""
        prompt = messages[-1].content if messages else ""
        if "깐깐한 리뷰어" in prompt:                             # 평가 에이전트
            keys = [c["key"] for c in load_judge_config()["criteria"]]
            msg = AIMessage(json.dumps({"scores": [{"key": k, "score": 4, "reason": "ok"} for k in keys],
                                        "issues": [], "revision_instructions": ""}))
        elif "'리포트 작성' 담당" in system:                      # 리포트 작성 에이전트
            self.calls.append(("writer", prompt))
            msg = AIMessage("# 종합 리포트\n**요약** — 정형·탐색 결과 통합 (교차 확인)")
        else:                                                    # 분석 에이전트
            role = next((r for r in SQL if f"당신의 역할: {r}" in system), None)
            if role and not any(isinstance(m, ToolMessage) for m in messages):
                msg = AIMessage("", tool_calls=[{"id": role, "name": "run_sql",
                                                 "args": {"sql": SQL[role], "purpose": f"{role} 조회"}}])
            else:
                self.calls.append(("memo", role))
                msg = AIMessage(f"분석 메모 ({role or '단일'})")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_multi_runs_both_agents_then_writer_and_judge(tmp_path):
    llm = RoleFakeLLM()
    llm.calls = []
    cfg = Settings()
    sdb = ServiceDB(str(tmp_path / "s.db"))
    events = []
    graph = build_graph(llm, get_connector("sqlite", cfg.warehouse_dsn), sdb, cfg, mode="multi", emit=events.append)
    out = graph.invoke({"question": "추석 이후 매출이 왜 줄었지?"})

    assert graph.mode == "multi" and graph.agent_names == ["정형 분석", "자율 탐색"]
    assert set(out["memos"]) == {"structured", "explorer"}
    names = {s["name"] for s in graph.workspace.sql_log}
    assert names == {"S_r1", "E_r1"} and set(graph.workspace.frames) == {"S_r1", "E_r1"}
    assert {s["agent"] for s in graph.workspace.sql_log} == {"정형 분석", "자율 탐색"}
    writer_prompt = next(p for kind, p in llm.calls if kind == "writer")
    assert "### 정형 분석 (결과 이름 앞에 S_)" in writer_prompt and "에이전트별 관점 차이" in writer_prompt
    assert out["report_md"].startswith("# 종합 리포트") and out["judge"]["passed"]
    assert any(e.startswith("[정형 분석] SQL 조회") for e in events) and any(e.startswith("[자율 탐색]") for e in events)
    assert sdb.report(out["report_id"])["judge"]["overall"] == 4


def test_single_mode_has_no_perspective_section(tmp_path):
    llm = RoleFakeLLM()
    llm.calls = []
    cfg = Settings()
    graph = build_graph(llm, get_connector("sqlite", cfg.warehouse_dsn), ServiceDB(str(tmp_path / "s.db")), cfg,
                        mode="single")
    out = graph.invoke({"question": "지난달 매출 얼마야?"})
    assert graph.agent_names == ["분석"] and list(out["memos"]) == ["analyst"]
    writer_prompt = next(p for kind, p in llm.calls if kind == "writer")
    assert "에이전트별 관점 차이" not in writer_prompt


def test_disabled_agent_is_skipped(tmp_path):
    acfg = load_agents()
    acfg["agents"] = [{**a, "enabled": a["id"] == "explorer"} for a in acfg["agents"]]
    cfg = Settings()
    graph = build_graph(RoleFakeLLM(), get_connector("sqlite", cfg.warehouse_dsn), ServiceDB(str(tmp_path / "s.db")),
                        cfg, mode="multi", agents_cfg=acfg)
    assert graph.agent_names == ["자율 탐색"] and graph.mode == "single"


def test_auto_mode_routing():
    assert resolve_mode("auto", "auto") == "single"      # 정기 자동 리포트
    assert resolve_mode("auto", "explore") == "multi"    # 자연어 질문
    assert resolve_mode("single", "explore") == "single"


def test_stream_analysis_events_and_result(tmp_path):
    cfg = Settings(service_db_path=str(tmp_path / "s.db"))
    events = []
    res = stream_analysis("광고비는 늘었는데 ROAS가 떨어졌다", "ad_efficiency", llm=RoleFakeLLM(), cfg=cfg,
                          mode="multi", on_event=events.append)
    assert res["mode"] == "multi" and res["agents"] == ["정형 분석", "자율 탐색"] and res["report_id"]
    assert set(res["memos"]) == {"structured", "explorer"}   # 병렬 에이전트 메모가 덮어써지지 않음
    assert res["steps"] == 4                                  # 두 에이전트 턴 수 합계 (각 2턴)
    assert events[0].startswith("실행 모드: 멀티 에이전트")
    assert any("평가 에이전트: 4.0점 · 통과" in e for e in events)
