"""사용법:
  python -m analyst.cli "광고비는 늘었는데 ROAS가 떨어졌다"
  python -m analyst.cli --agenda ad_efficiency
  python -m analyst.cli "추석 이후 매출이 왜 줄었지?" --mode multi --show-memos
  python -m analyst.cli --list-agendas
"""
from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()

from .config import settings  # noqa: E402
from .graph import run_analysis  # noqa: E402
from .service_db import ServiceDB  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="AI Data Analyst Agent")
    ap.add_argument("question", nargs="?", default="")
    ap.add_argument("--agenda", default=None, help="아젠다 ID (예: weekly_sales, ad_efficiency)")
    ap.add_argument("--list-agendas", action="store_true")
    ap.add_argument("--show-sql", action="store_true")
    ap.add_argument("--mode", default=None, choices=["auto", "single", "multi"],
                    help="auto(목적별 자동, 기본) | single(분석 에이전트 1개) | multi(정형·탐색 동시 실행)")
    ap.add_argument("--show-memos", action="store_true", help="분석 에이전트별 메모 출력")
    a = ap.parse_args()
    if a.list_agendas:
        for g in ServiceDB(settings.service_db_path).agendas():
            print(f'{g["id"]:15s} {g["title"]} — {g["question"]} ({" → ".join(s["name"] for s in g["steps"])})')
        return
    if not a.question and not a.agenda:
        ap.error("질문 또는 --agenda 가 필요합니다.")
    res = run_analysis(a.question, a.agenda, mode=a.mode, on_event=lambda t: print("  ·", t))
    if a.show_sql:
        for s in res["sql_log"]:
            print(f'-- [{s.get("agent", "")} {s.get("name", "ERR")}] {s["purpose"]}\n{s["sql"]}\n')
    if a.show_memos:
        for m in res["memos"].values():
            print(f'===== {m["name"]} 메모 =====\n{m["memo"]}\n')
    print(res["report_md"])
    j = res.get("judge")
    if j and j.get("passed") is not None:
        print(f'\n[품질 판정] {j["overall"]}점 · {"통과" if j["passed"] else "기준 미달"} · 재작성 {j.get("revision", 0)}회')
        for sc in j.get("scores", []):
            print(f'  - {sc["key"]}: {sc["score"]} ({sc.get("reason", "")})')
    print(f'\n(report_id={res["report_id"]}, 모드={res["mode"]}: {", ".join(res["agents"])}, '
          f'단계={res["steps"]}, 쿼리={len(res["sql_log"])}개)')


if __name__ == "__main__":
    main()
