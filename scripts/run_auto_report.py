"""자동 리포트 생성 (작업 스케줄러/cron 등록용).

  python scripts/run_auto_report.py --agenda weekly_sales
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from analyst.graph import stream_analysis  # noqa: E402
from analyst.service_db import ServiceDB  # noqa: E402
from analyst.config import settings  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--agenda", default="weekly_sales")
a = ap.parse_args()
ag = ServiceDB(settings.service_db_path).agenda(a.agenda)
if not ag:
    sys.exit(f"아젠다 없음: {a.agenda}")
res = stream_analysis(f'[자동 리포트] {ag["title"]}: {ag["question"]}', a.agenda, kind="auto",
                      on_event=lambda t: print("·", t))
print(f'\n저장 완료 report_id={res["report_id"]}')
