"""저장된 리포트를 LLM judge로 다시 채점합니다 (그래프 밖에서 쓰는 '별도 판정 에이전트').

리포트에 남아 있는 SQL을 다시 실행해 근거 자료를 만들고, config/judge.yaml 기준으로 채점해 서비스 DB에 기록합니다.
프롬프트(analyst/prompts.py)나 모델을 바꾼 뒤 품질이 좋아졌는지 비교할 때 씁니다.

  python scripts/judge_reports.py                 # 최근 리포트 10건
  python scripts/judge_reports.py --limit 30
  python scripts/judge_reports.py --report-id 1a2b3c4d5e6f
  python scripts/judge_reports.py --only-missing  # 아직 채점 안 된 것만
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from analyst.config import settings  # noqa: E402
from analyst.connectors import get_connector  # noqa: E402
from analyst.judge import build_evidence, check_numbers, judge_report, load_judge_config  # noqa: E402
from analyst.llm import get_judge_llm  # noqa: E402
from analyst.service_db import ServiceDB  # noqa: E402
from analyst.sql_guard import validate_sql  # noqa: E402


def rebuild_frames(conn, sql_log: list[dict]) -> dict:
    """리포트에 기록된 SQL을 같은 안전장치(sql_guard)를 거쳐 다시 실행합니다."""
    frames = {}
    for s in sql_log:
        if "error" in s or not s.get("name"):
            continue
        try:
            safe = validate_sql(s["sql"], conn.dialect, settings.allowed_table_prefixes, settings.max_rows)
            frames[s["name"]] = conn.execute(safe, settings.max_rows).df
        except Exception as e:  # noqa: BLE001
            print(f'  ! {s["name"]} 재실행 실패: {e}')
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--report-id", default=None)
    ap.add_argument("--only-missing", action="store_true")
    a = ap.parse_args()

    sdb = ServiceDB(settings.service_db_path)
    conn = get_connector(settings.warehouse_connector, settings.warehouse_dsn)
    jcfg, llm = load_judge_config(), get_judge_llm(settings)
    metrics = "\n".join(f'- {m["metric_key"]}: {m["definition"]} | {m["formula_sql"]}' for m in sdb.metrics())
    ids = [a.report_id] if a.report_id else [r["report_id"] for r in sdb.reports(limit=a.limit)]

    rows = []
    for rid in ids:
        r = sdb.report(rid)
        if not r or (a.only_missing and r.get("judge")):
            continue
        frames = rebuild_frames(conn, r["sql_log"])
        nc = check_numbers(r["report_md"], frames, tolerance=(jcfg.get("number_check") or {}).get("tolerance", 0.01))
        res = judge_report(llm, r["question"], r["report_md"], build_evidence(r["sql_log"], frames),
                           metrics=metrics, number_check=nc, cfg=jcfg)
        sdb.save_judge(rid, res)
        title = (r["report_md"] or "").splitlines()[0].lstrip("# ")[:40] if r["report_md"] else "(내용 없음)"
        verdict = "오류" if res.get("passed") is None else ("통과" if res["passed"] else "불통과")
        rows.append((rid, res.get("overall", "-"), verdict, title))
        print(f'{rid}  {res.get("overall", "-"):>4}  {verdict:3s}  {title}')
        for i in res.get("issues", [])[:3]:
            print(f"    - {i}")
        if res.get("error"):
            print(f'    ! {res["error"]}')

    scored = [o for _, o, v, _ in rows if v != "오류"]
    if scored:
        print(f"\n평균 {sum(scored) / len(scored):.2f}점 · 통과 {sum(v == '통과' for *_, v, _ in rows)}/{len(rows)}건")


if __name__ == "__main__":
    main()
