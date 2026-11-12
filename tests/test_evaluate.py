"""배포 전 평가 로직 테스트 — 에이전트 실행을 가짜 함수로 대체해 LLM 없이 실행."""
from analyst.evaluate import load_cases, mention_check, run_eval, to_markdown
from analyst.judge import load_judge_config

KEYS = [c["key"] for c in load_judge_config()["criteria"]]


def _res(score, report="# META ROAS 하락", first=None):
    return {"report_md": report, "sql_log": [{}], "judge": {
        "overall": score, "passed": score >= 3.5, "revision": 0 if first is None else 1,
        "scores": [{"key": k, "score": round(score)} for k in KEYS],
        "history": [{"overall": first if first is not None else score}]}}


def test_questions_yaml_is_valid():
    cases, th = load_cases()
    assert len(cases) >= 4 and len({c["id"] for c in cases}) == len(cases)
    assert {"avg_score", "case_score"} <= set(th)


def test_mention_check_any_of():
    assert mention_check("린넨 오버핏 셔츠 반품률 36%", [["린넨", "P007"], "반품률"]) == (True, [])
    assert mention_check("반품률 상승", [["린넨", "P007"], "반품률"]) == (False, ["린넨/P007"])


def test_eval_passes_when_scores_high():
    cases = [{"id": "a", "question": "q", "must_mention": ["META"]}, {"id": "b", "question": "q"}]
    out = run_eval(cases, {"avg_score": 3.8}, lambda c: _res(4.5, first=3.0))
    s = out["summary"]
    assert s["passed"] and s["avg_score"] == 4.5 and s["avg_first_score"] == 3.0 and s["cases_passed"] == 2
    assert "✅ 통과" in to_markdown(out)


def test_eval_fails_on_low_score_missing_word_and_crash():
    def run(c):
        if c["id"] == "boom":
            raise RuntimeError("API 오류")
        return _res(2.0 if c["id"] == "low" else 4.5, report="# 리포트")
    cases = [{"id": "low", "question": "q"}, {"id": "word", "question": "q", "must_mention": ["META"]},
             {"id": "boom", "question": "q"}]
    out = run_eval(cases, {}, run)
    f = " ".join(out["summary"]["failures"])
    assert not out["summary"]["passed"]
    assert "low: 점수 2.0" in f and "word(META)" in f and "boom: 실행/판정 실패" in f


def test_eval_fails_when_judge_did_not_run():
    out = run_eval([{"id": "x", "question": "q"}], {}, lambda c: {"report_md": "r", "judge": {"passed": None, "error": "파싱 실패"}})
    assert not out["summary"]["passed"] and "파싱 실패" in out["summary"]["failures"][0]
