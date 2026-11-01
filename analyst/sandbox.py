"""Python 분석 실행기 (v1: 프로세스 내 제한 실행).

주의: exec 기반이라 완전한 보안 격리가 아닙니다. 전사 배포 전에는
컨테이너/서브프로세스 샌드박스(예: 별도 워커, gVisor, E2B 등)로 교체해야 합니다.
"""
from __future__ import annotations

import contextlib
import io
import multiprocessing as mp
import traceback

import numpy as np
import pandas as pd

_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in ["abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list", "max", "min",
              "print", "range", "round", "set", "sorted", "str", "sum", "tuple", "zip", "isinstance", "map",
              "filter", "reversed", "Exception", "ValueError", "KeyError"]
}
_BANNED_TOKENS = ("import ", "__", "open(", "exec(", "eval(", "compile(", "globals(", "locals(", "getattr(")


def _run(code: str, frames: dict[str, pd.DataFrame], q: mp.Queue) -> None:
    buf = io.StringIO()
    env = {"__builtins__": _SAFE_BUILTINS, "pd": pd, "np": np, **frames}
    try:
        with contextlib.redirect_stdout(buf):
            exec(code, env, env)
        q.put({"ok": True, "stdout": buf.getvalue()})
    except Exception:
        q.put({"ok": False, "stdout": buf.getvalue(), "error": traceback.format_exc(limit=1)})


def run_python(code: str, frames: dict[str, pd.DataFrame], timeout: int = 20, max_output: int = 6000) -> str:
    for tok in _BANNED_TOKENS:
        if tok in code:
            return f"ERROR: 허용되지 않는 구문 '{tok.strip()}' (pd, np와 제공된 DataFrame만 사용하세요)"
    ctx = mp.get_context("spawn")  # Windows·macOS·Linux 공통 동작
    q: mp.Queue = ctx.Queue()
    p = ctx.Process(target=_run, args=(code, frames, q))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.kill()
        return f"ERROR: 실행 시간 초과({timeout}s)"
    res = q.get() if not q.empty() else {"ok": False, "stdout": "", "error": "결과 없음"}
    out = res["stdout"][:max_output] or "(출력 없음 — print()로 결과를 출력하세요)"
    return out if res["ok"] else f"{out}\nERROR: {res['error']}"
