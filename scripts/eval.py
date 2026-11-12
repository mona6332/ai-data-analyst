"""배포 전 품질 평가: 고정 질문 세트(eval/questions.yaml)로 에이전트를 돌리고 판사 LLM으로 채점합니다.

  python scripts/eval.py                          # 전체 질문
  python scripts/eval.py --only ad_roas_drop      # 일부만 (쉼표로 여러 개)
  python scripts/eval.py --baseline eval/results/20261112-130000.json   # 이전 결과와 비교

통과 기준(eval/questions.yaml 의 thresholds)을 못 넘으면 종료 코드 1 → 배포·머지 차단에 사용.
결과는 eval/results/<시각>.json, .md 로 저장됩니다. 평가 리포트는 분석 아카이브(서비스 DB)에 섞이지 않게
임시 서비스 DB에 저장합니다. 질문 수 × (분석 + 판정) 만큼 LLM 비용이 듭니다.
"""
import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from analyst.config import settings  # noqa: E402
from analyst.evaluate import load_cases, run_eval, save_result, to_markdown  # noqa: E402
from analyst.graph import stream_analysis  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="실행할 질문 id (쉼표 구분)")
    ap.add_argument("--baseline", default=None, help="비교할 이전 결과 json")
    ap.add_argument("--questions", default=None, help="질문 세트 yaml (기본 eval/questions.yaml)")
    a = ap.parse_args()

    cases, thresholds = load_cases(Path(a.questions) if a.questions else None)
    if a.only:
        ids = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in ids]
    if not cases:
        print("실행할 질문이 없습니다.")
        return 1
    if not settings.judge_enabled:
        print("JUDGE_ENABLED=false 이면 평가할 수 없습니다.")
        return 1

    tmp = tempfile.mkdtemp(prefix="eval_")
    cfg = replace(settings, service_db_path=str(Path(tmp) / "service.db"))
    meta = {"모델": cfg.llm_model, "판사": cfg.judge_model or cfg.llm_model, "질문": len(cases)}
    print(f"평가 시작 · {meta}")

    def run(case):
        return stream_analysis(case["question"], case.get("agenda"), kind="eval", cfg=cfg)

    def on_case(r):
        mark = "실패" if r.get("error") or r.get("judge_error") else f'{r["first_score"]} → {r["final_score"]}점'
        print(f'  · {r["id"]:18s} {mark}  ({r["seconds"]}s, 쿼리 {r.get("queries", 0)}개)'
              + (f'  핵심 단어 누락: {", ".join(r["missing_mentions"])}' if r.get("missing_mentions") else ""))

    out = run_eval(cases, thresholds, run, on_case)
    out["meta"] = meta
    baseline = json.loads(Path(a.baseline).read_text(encoding="utf-8")) if a.baseline else None
    md = to_markdown(out, meta, baseline)
    path = save_result(out, md)
    print("\n" + md)
    print(f"저장: {path} (.md 동일 이름)")
    return 0 if out["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
