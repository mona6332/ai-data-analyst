"""사용법:
  python -m analyst.cli "광고비는 늘었는데 ROAS가 떨어졌다"
  python -m analyst.cli --agenda ad_efficiency
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
    a = ap.parse_args()
    if a.list_agendas:
        for g in ServiceDB(settings.service_db_path).agendas():
            print(f'{g["id"]:15s} {g["title"]} — {g["question"]} ({" → ".join(s["name"] for s in g["steps"])})')
        return
    if not a.question and not a.agenda:
        ap.error("질문 또는 --agenda 가 필요합니다.")
    res = run_analysis(a.question, a.agenda, on_event=lambda t: print("  ·", t))
    if a.show_sql:
        for s in res["sql_log"]:
            print(f'-- [{s.get("name", "ERR")}] {s["purpose"]}\n{s["sql"]}\n')
    print(res["report_md"])
    print(f'\n(report_id={res["report_id"]}, 단계={res["steps"]}, 쿼리={len(res["sql_log"])}개)')


if __name__ == "__main__":
    main()
