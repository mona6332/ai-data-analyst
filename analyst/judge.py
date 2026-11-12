"""리포트 품질 판정 (LLM-as-a-judge).

분석 에이전트와 분리된 모듈입니다. 두 곳에서 같은 코드를 씁니다.
  1) 그래프 안: write_report → judge → (불통과 시 재작성) → save_report   (analyst/graph.py)
  2) 그래프 밖: 저장된 리포트 일괄 채점                                  (scripts/judge_reports.py)

판정은 두 단계입니다.
  - 규칙 검사: 리포트 속 숫자가 실제 조회·분석 결과에 있는지 코드로 대조 (LLM 호출 없음)
  - LLM 채점: config/judge.yaml 의 기준별 1~5점 + 문제점 + 재작성 지시
"""
from __future__ import annotations

import json
import re
from typing import Iterable

import numpy as np
import pandas as pd
import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from .config import ROOT
from .prompts import JUDGE_PROMPT

DEFAULT_CONFIG = {
    "enabled": True, "pass_score": 3.5, "min_criterion_score": 2, "max_revisions": 1,
    "number_check": {"enabled": True, "tolerance": 0.01}, "criteria": [],
}


def load_judge_config(path=None) -> dict:
    p = path or ROOT / "config" / "judge.yaml"
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        cfg.update(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    return cfg


# ---------------------------------------------------------------- 근거 자료
def build_evidence(sql_log: list[dict], frames: dict[str, pd.DataFrame], python_log: list[dict] | None = None,
                   max_rows: int = 40, max_chars: int = 15000) -> str:
    """판사에게 줄 '실제 조회·분석 결과' 텍스트."""
    parts = []
    for s in sql_log:
        if "error" in s:
            continue
        df = frames.get(s.get("name", ""))
        table = df.head(max_rows).to_markdown(index=False) if df is not None and len(df) else "(0 rows)"
        parts.append(f'### [{s.get("name")}] {s.get("purpose", "")}\n```sql\n{s["sql"]}\n```\n{table}')
    for i, p in enumerate(python_log or [], 1):
        parts.append(f"### Python 분석 {i}\n```python\n{p['code']}\n```\n출력:\n{p['output']}")
    text = "\n\n".join(parts) or "(조회 결과 없음)"
    return text[:max_chars] + ("\n...(이하 생략)" if len(text) > max_chars else "")


# ---------------------------------------------------------------- 규칙 검사: 숫자 대조
_NUM = re.compile(r"(?<![\w.])([+-]?\d{1,3}(?:,\d{3})+|[+-]?\d+(?:\.\d+)?)\s*(%|%p|억|만|천)?")
_UNIT = {"억": 1e8, "만": 1e4, "천": 1e3}
_DATE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|\d{1,2}/\d{1,2}|\d{4}년|\d{1,2}월|\d{1,2}일|M\+\d+|W\d+|P\d{3}")


def _report_numbers(md: str) -> list[tuple[str, float, str]]:
    """리포트에서 검증 대상 숫자 추출: (원문, 값, 단위). 날짜·목차 번호·작은 정수는 제외."""
    out = []
    for line in md.splitlines():
        line = re.sub(r"^\s*(\d+\.|[-*#]+)\s*", "", line)   # 목록 번호 제거
        line = _DATE.sub(" ", line)
        for m in _NUM.finditer(line):
            raw, unit = m.group(1), m.group(2) or ""
            v = float(raw.replace(",", ""))
            if unit in _UNIT:
                v *= _UNIT[unit]
            if not unit and abs(v) <= 12 and float(v).is_integer():   # '3개', '2주' 같은 작은 정수
                continue
            out.append((m.group(0).strip(), v, unit))
    return out


def _evidence_numbers(frames: dict[str, pd.DataFrame], python_log: list[dict] | None) -> list[float]:
    vals: list[float] = []
    for df in frames.values():
        num = df.select_dtypes("number")
        vals += [float(x) for x in num.to_numpy().ravel() if pd.notna(x)]
    for p in python_log or []:
        vals += [float(x.replace(",", "")) for x in re.findall(r"[+-]?\d[\d,]*\.?\d*", p.get("output", ""))
                 if x.replace(",", "").replace(".", "").lstrip("+-").isdigit()]
    return vals


def _decimals(x: float) -> int:
    s = repr(x)
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def _candidates(ev: list[float], max_pair: int = 300) -> np.ndarray:
    """결과값 + 파생값(×100, 비율, 증감률, 차이). 정렬된 배열로 반환."""
    base = np.unique(np.array(ev, dtype=float)) if ev else np.array([])
    pair = base if len(base) <= max_pair else base[np.linspace(0, len(base) - 1, max_pair).astype(int)]
    a, b = np.meshgrid(pair, pair)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(b != 0, a / b, np.nan)
    allc = np.concatenate([base, base * 100, ratio.ravel(), ratio.ravel() * 100, (ratio.ravel() - 1) * 100,
                           (a - b).ravel()])
    return np.sort(allc[np.isfinite(allc)])


def _found(t: float, cand: np.ndarray, tol: float) -> bool:
    width = max(abs(t) * tol, 0.5 * 10 ** (-_decimals(t))) + 1e-9   # 상대 오차 또는 반올림 자릿수
    i = np.searchsorted(cand, t - width)
    return i < len(cand) and cand[i] <= t + width


def check_numbers(report_md: str, frames: dict[str, pd.DataFrame], python_log: list[dict] | None = None,
                  tolerance: float = 0.01) -> dict:
    """리포트 숫자 중 조회·분석 결과로 설명되지 않는 것을 찾습니다.

    결과값 그대로, 비율×100(%), 두 값의 비율·증감률·차이까지 허용합니다. 완벽하지 않은 휴리스틱이므로
    불통과의 직접 근거로 쓰지 않고 판사 LLM에 참고로 전달합니다.
    """
    cand = _candidates(_evidence_numbers(frames, python_log))
    checked, unverified = 0, []
    for raw, v, unit in _report_numbers(report_md):
        checked += 1
        targets = [v, abs(v)] + ([v / 100] if unit in ("%", "%p") else [])
        if not any(_found(t, cand, tolerance) for t in targets):
            unverified.append(raw)
    return {"checked": checked, "unverified": list(dict.fromkeys(unverified))}


# ---------------------------------------------------------------- LLM 채점
def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("판사 응답에서 JSON을 찾지 못했습니다.")
    return json.loads(m.group(0))


def _content(resp) -> str:
    c = resp.content
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c if isinstance(b, dict))


def _criteria_text(criteria: Iterable[dict]) -> str:
    return "\n".join(f'- {c["key"]} ({c["name"]}, 가중치 {c.get("weight", 1)}): {c["description"]}' for c in criteria)


def score(result: dict, cfg: dict) -> dict:
    """기준별 점수 → 가중 평균·통과 여부 계산 (코드에서 계산해 LLM 산수 오류를 피함)."""
    weights = {c["key"]: float(c.get("weight", 1)) for c in cfg["criteria"]}
    scores = {s["key"]: int(s["score"]) for s in result.get("scores", []) if s.get("key") in weights}
    if not scores:
        raise ValueError("판사 응답에 유효한 점수가 없습니다.")
    total_w = sum(weights[k] for k in scores)
    overall = round(sum(scores[k] * weights[k] for k in scores) / total_w, 2)
    passed = overall >= cfg["pass_score"] and min(scores.values()) >= cfg["min_criterion_score"]
    return {**result, "overall": overall, "passed": passed}


def judge_report(llm: BaseChatModel, question: str, report_md: str, evidence: str, metrics: str = "",
                 number_check: dict | None = None, cfg: dict | None = None) -> dict:
    """리포트 1건 판정. 반환: {scores, issues, revision_instructions, overall, passed, number_check}

    판사 호출·파싱이 실패하면 passed=None 과 error 를 돌려줍니다 (분석 자체는 막지 않음).
    """
    cfg = cfg or load_judge_config()
    nc = number_check or {"checked": 0, "unverified": []}
    nc_text = (f'숫자 {nc["checked"]}개 검사, 결과에서 찾지 못한 숫자: {", ".join(nc["unverified"]) or "없음"}'
               if nc["checked"] else "검사하지 않음")
    prompt = JUDGE_PROMPT.format(question=question or "(아젠다 정기 분석)", metrics=metrics or "(생략)",
                                 evidence=evidence, number_check=nc_text, report=report_md,
                                 criteria=_criteria_text(cfg["criteria"]))
    try:
        result = score(_parse_json(_content(llm.invoke([HumanMessage(prompt)]))), cfg)
    except Exception as e:  # noqa: BLE001 — 판정 실패가 분석 저장을 막지 않도록
        return {"passed": None, "error": f"{type(e).__name__}: {e}", "number_check": nc}
    result["number_check"] = nc
    return result


def feedback_text(result: dict) -> str:
    lines = [f'- [{s["key"]}] {s["score"]}점: {s.get("reason", "")}' for s in result.get("scores", [])
             if s.get("score", 5) < 4]
    lines += [f"- {i}" for i in result.get("issues", [])]
    if result.get("number_check", {}).get("unverified"):
        lines.append("- 조회 결과에서 확인되지 않는 숫자: " + ", ".join(result["number_check"]["unverified"]))
    if result.get("revision_instructions"):
        lines.append("\n수정 지시: " + result["revision_instructions"])
    return "\n".join(lines)
