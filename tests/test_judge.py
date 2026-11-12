"""리포트 품질 판정(LLM judge) 테스트 — 가짜 모델로 LLM 없이 실행."""
import json
import sqlite3

import pandas as pd
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from analyst.config import Settings
from analyst.connectors import get_connector
from analyst.graph import build_graph
from analyst.judge import check_numbers, judge_report, load_judge_config, score
from analyst.service_db import ServiceDB


class FakeToolLLM(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kw):
        return self


def _verdict(points: int, instructions: str = "") -> AIMessage:
    keys = [c["key"] for c in load_judge_config()["criteria"]]
    return AIMessage(content=json.dumps({
        "scores": [{"key": k, "score": points, "reason": "테스트"} for k in keys],
        "issues": [] if points >= 4 else ["근거 없는 수치 사용"],
        "revision_instructions": instructions}, ensure_ascii=False))


def test_check_numbers_flags_made_up_values():
    frames = {"r1": pd.DataFrame({"channel": ["META", "NAVER"], "spend": [1550000, 800000],
                                  "roas": [3.04, 4.21]})}
    md = ("# META 효율 하락\n- META 광고비 1,550,000원, ROAS 3.04 (NAVER 4.21)\n"
          "- META 광고비는 NAVER의 193.8% 수준\n- 전환율 7.7% 하락")
    nc = check_numbers(md, frames)
    assert nc["checked"] >= 4
    assert nc["unverified"] == ["7.7%"]          # 결과에 없는 숫자만 걸림 (193.8% 는 비율로 설명됨)


def test_score_weighted_and_min_rule():
    cfg = load_judge_config()
    keys = [c["key"] for c in cfg["criteria"]]
    good = score({"scores": [{"key": k, "score": 4} for k in keys]}, cfg)
    assert good["overall"] == 4 and good["passed"]
    one_bad = score({"scores": [{"key": k, "score": 1 if k == "grounding" else 5} for k in keys]}, cfg)
    assert not one_bad["passed"]                 # 평균은 높아도 한 기준이 최저 점수 미만이면 불통과


def test_judge_failure_does_not_block():
    llm = FakeMessagesListChatModel(responses=[AIMessage(content="JSON 아님")])
    out = judge_report(llm, "q", "# r", "(근거)")
    assert out["passed"] is None and "error" in out


def test_graph_revises_until_judge_passes(tmp_path):
    llm = FakeToolLLM(responses=[
        AIMessage(content="", tool_calls=[{"id": "1", "name": "run_sql", "args": {
            "sql": "SELECT channel, SUM(attributed_revenue)*1.0/SUM(spend) AS roas FROM g1_fact_marketing_daily "
                   "GROUP BY 1", "purpose": "채널별 ROAS"}}]),
        AIMessage(content="분석 메모"),
        AIMessage(content="# 리포트 v1\nROAS 9.99"),
        AIMessage(content="# 리포트 v2\nMETA ROAS 최저"),
    ])
    judge_llm = FakeMessagesListChatModel(responses=[_verdict(2, "근거 수치로 다시 쓰세요"), _verdict(5)])
    cfg = Settings()
    sdb = ServiceDB(str(tmp_path / "service.db"))
    graph = build_graph(llm, get_connector("sqlite", cfg.warehouse_dsn), sdb, cfg, judge_llm=judge_llm)
    out = graph.invoke({"question": "ROAS가 떨어졌다", "agenda_id": "ad_efficiency"})

    assert out["report_md"].startswith("# 리포트 v2") and out["revisions"] == 1
    assert out["judge"]["passed"] and [h["passed"] for h in out["judge"]["history"]] == [False, True]
    saved = sdb.report(out["report_id"])
    assert saved["judge"]["overall"] == 5


def test_graph_stops_after_max_revisions(tmp_path):
    llm = FakeToolLLM(responses=[AIMessage(content="분석 메모"), AIMessage(content="# 리포트")])
    judge_llm = FakeMessagesListChatModel(responses=[_verdict(1, "다시")])
    cfg = Settings()
    jcfg = {**load_judge_config(), "max_revisions": 2}
    graph = build_graph(llm, get_connector("sqlite", cfg.warehouse_dsn), ServiceDB(str(tmp_path / "s.db")), cfg,
                        judge_llm=judge_llm, judge_cfg=jcfg)
    out = graph.invoke({"question": "q"})
    assert out["revisions"] == 2 and out["judge"]["passed"] is False and out["report_id"]


def test_service_db_migrates_old_reports_table(tmp_path):
    p = tmp_path / "old.db"
    with sqlite3.connect(p) as con:
        con.execute("CREATE TABLE analysis_reports(report_id TEXT PRIMARY KEY, created_at TEXT, kind TEXT, "
                    "agenda_id TEXT, question TEXT, report_md TEXT, sql_log_json TEXT, charts_json TEXT, status TEXT)")
        con.execute("INSERT INTO analysis_reports VALUES ('old1','2026-11-01','explore',NULL,'q','# r','[]','[]','done')")
    sdb = ServiceDB(str(p))
    assert sdb.report("old1")["judge"] is None
    sdb.save_judge("old1", {"overall": 4.2, "passed": True})
    assert sdb.report("old1")["judge"]["overall"] == 4.2
