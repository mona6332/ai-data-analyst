"""배포 전 품질 평가 (고정 질문 세트 × 에이전트 × LLM judge).

  리포트 저장 전 판정 (graph.py 의 judge 노드)  → 리포트 1건의 품질을 보고 재작성
  배포 전 평가 (이 모듈, scripts/eval.py)        → 프롬프트·모델·코드를 바꾼 버전이 전체적으로 좋아졌는지 측정

질문 세트와 통과 기준은 eval/questions.yaml 에 있습니다.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from .config import ROOT
from .judge import load_judge_config

EVAL_DIR = ROOT / "eval"


def load_cases(path: Path | None = None) -> tuple[list[dict], dict]:
    data = yaml.safe_load((path or EVAL_DIR / "questions.yaml").read_text(encoding="utf-8")) or {}
    return data.get("cases", []), data.get("thresholds", {})


def mention_check(report_md: str, must: list) -> tuple[bool, list[str]]:
    """must_mention 검사. 항목이 목록이면 그중 하나만 있어도 통과. 반환: (통과, 빠진 항목)"""
    text = (report_md or "").lower()
    missing = []
    for item in must or []:
        options = item if isinstance(item, list) else [item]
        if not any(str(o).lower() in text for o in options):
            missing.append("/".join(map(str, options)))
    return not missing, missing


def evaluate_case(case: dict, run: Callable[[dict], dict]) -> dict:
    """질문 1건 실행·채점. run(case) 는 stream_analysis 와 같은 형태의 dict 를 돌려줍니다."""
    t0 = time.time()
    try:
        res = run(case)
    except Exception as e:  # noqa: BLE001 — 한 질문 실패가 전체 평가를 멈추지 않도록
        return {"id": case["id"], "question": case["question"], "error": f"{type(e).__name__}: {e}",
                "seconds": round(time.time() - t0, 1), "final_score": None, "passed": False}
    j = res.get("judge") or {}
    ok_mention, missing = mention_check(res.get("report_md", ""), case.get("must_mention"))
    history = j.get("history") or []
    return {
        "id": case["id"], "question": case["question"], "agenda": case.get("agenda"),
        "seconds": round(time.time() - t0, 1), "queries": len(res.get("sql_log") or []),
        "first_score": history[0].get("overall") if history else j.get("overall"),
        "final_score": j.get("overall"), "judge_passed": j.get("passed"), "revisions": j.get("revision", 0),
        "scores": {s["key"]: s["score"] for s in j.get("scores", [])},
        "issues": j.get("issues", []), "unverified_numbers": (j.get("number_check") or {}).get("unverified", []),
        "mention_ok": ok_mention, "missing_mentions": missing,
        "judge_error": (j.get("error") or "판정 결과 없음") if j.get("passed") is None else None,
        "report_md": res.get("report_md", ""),
    }


def summarize(results: list[dict], thresholds: dict) -> dict:
    """전체 점수·기준별 평균을 내고 통과 기준과 비교합니다."""
    th = {"avg_score": 3.8, "case_score": 3.0, "criterion_avg": 3.0, "must_mention_rate": 1.0, **(thresholds or {})}
    scored = [r for r in results if r.get("final_score") is not None]
    keys = [c["key"] for c in load_judge_config()["criteria"]]
    per_criterion = {k: round(statistics.mean(r["scores"][k] for r in scored if k in r["scores"]), 2)
                     for k in keys if any(k in r["scores"] for r in scored)}
    avg = round(statistics.mean(r["final_score"] for r in scored), 2) if scored else None
    first = [r["first_score"] for r in scored if r.get("first_score") is not None]
    mention_rate = round(sum(r.get("mention_ok", False) for r in results) / len(results), 2) if results else 0

    failures = []
    for r in results:
        if r.get("error") or r.get("judge_error"):
            failures.append(f'{r["id"]}: 실행/판정 실패 ({r.get("error") or r.get("judge_error")})')
        elif r["final_score"] < th["case_score"]:
            failures.append(f'{r["id"]}: 점수 {r["final_score"]} < {th["case_score"]}')
    if avg is None or avg < th["avg_score"]:
        failures.append(f'전체 평균 {avg} < {th["avg_score"]}')
    for k, v in per_criterion.items():
        if v < th["criterion_avg"]:
            failures.append(f"기준 {k} 평균 {v} < {th['criterion_avg']}")
    if mention_rate < th["must_mention_rate"]:
        miss = [f'{r["id"]}({", ".join(r["missing_mentions"])})' for r in results if r.get("missing_mentions")]
        failures.append(f'핵심 단어 포함률 {mention_rate} < {th["must_mention_rate"]}: {"; ".join(miss)}')
    for r in results:
        r["passed"] = (not r.get("error") and not r.get("judge_error") and r["final_score"] >= th["case_score"]
                       and r.get("mention_ok", False))
    return {"avg_score": avg, "avg_first_score": round(statistics.mean(first), 2) if first else None,
            "per_criterion": per_criterion, "must_mention_rate": mention_rate,
            "cases": len(results), "cases_passed": sum(r["passed"] for r in results),
            "thresholds": th, "failures": failures, "passed": not failures}


def run_eval(cases: list[dict], thresholds: dict, run: Callable[[dict], dict],
             on_case: Callable[[dict], None] | None = None) -> dict:
    results = []
    for c in cases:
        r = evaluate_case(c, run)
        results.append(r)
        if on_case:
            on_case(r)
    return {"created_at": datetime.now().isoformat(timespec="seconds"), "summary": summarize(results, thresholds),
            "results": results}


def to_markdown(out: dict, meta: dict | None = None, baseline: dict | None = None) -> str:
    s = out["summary"]
    bs = (baseline or {}).get("summary", {})

    def delta(cur, prev):
        return f" ({cur - prev:+.2f})" if isinstance(cur, (int, float)) and isinstance(prev, (int, float)) else ""

    lines = [f'# 품질 평가 {"✅ 통과" if s["passed"] else "❌ 실패"}', ""]
    if meta:
        lines += [" · ".join(f"{k}: {v}" for k, v in meta.items()), ""]
    lines += [f'- 전체 평균 **{s["avg_score"]}**{delta(s["avg_score"], bs.get("avg_score"))} / 5 '
              f'(재작성 전 {s["avg_first_score"]}) · 기준 {s["thresholds"]["avg_score"]}',
              f'- 통과 질문 {s["cases_passed"]}/{s["cases"]} · 핵심 단어 포함률 {s["must_mention_rate"]}', "",
              "## 기준별 평균", "| 기준 | 평균 |", "|---|---|"]
    lines += [f"| {k} | {v}{delta(v, bs.get('per_criterion', {}).get(k))} |" for k, v in s["per_criterion"].items()]
    lines += ["", "## 질문별", "| 질문 | 최종 점수 | 재작성 | 핵심 단어 | 시간(초) | 결과 |", "|---|---|---|---|---|---|"]
    for r in out["results"]:
        mention = "OK" if r.get("mention_ok") else "누락: " + ", ".join(r.get("missing_mentions", []))
        lines.append(f'| {r["id"]} | {r.get("final_score", "-")} | {r.get("revisions", "-")} | {mention} | '
                     f'{r.get("seconds")} | {"통과" if r["passed"] else "실패"} |')
    if s["failures"]:
        lines += ["", "## 실패 사유"] + [f"- {f}" for f in s["failures"]]
    return "\n".join(lines) + "\n"


def save_result(out: dict, md: str, results_dir: Path | None = None) -> Path:
    d = results_dir or EVAL_DIR / "results"
    d.mkdir(parents=True, exist_ok=True)
    stem = datetime.now().strftime("%Y%m%d-%H%M%S")
    (d / f"{stem}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / f"{stem}.md").write_text(md, encoding="utf-8")
    return d / f"{stem}.json"
