"""지표·아젠다 정의 파일(config/*.yaml) 읽기/쓰기. 수정은 YAML 또는 '지표 관리' 화면에서."""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import ROOT

CONFIG_DIR = ROOT / "config"
METRIC_FIELDS = ["key", "name", "group", "definition", "formula", "source", "direction", "alert_pct", "format"]


def _load(name: str) -> list[dict]:
    p = CONFIG_DIR / name
    return yaml.safe_load(p.read_text(encoding="utf-8")) or [] if p.exists() else []


def _save(name: str, items: list[dict], header: str) -> None:
    body = yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=200)
    (CONFIG_DIR / name).write_text(header + "\n" + body, encoding="utf-8")


def load_metrics() -> list[dict]:
    return [{f: m.get(f) for f in METRIC_FIELDS} for m in _load("metrics.yaml")]


def load_agendas() -> list[dict]:
    out = []
    for a in _load("agendas.yaml"):
        a.setdefault("metrics", [])
        a.setdefault("starters", [])
        a["steps"] = [s if isinstance(s, dict) else {"name": str(s), "guide": ""} for s in a.get("steps", [])]
        out.append(a)
    return out


def save_metrics(items: list[dict]) -> None:
    _save("metrics.yaml", items, "# 지표 사전 — '지표 관리' 화면에서 저장됨. 직접 수정해도 됩니다.")


def save_agendas(items: list[dict]) -> None:
    _save("agendas.yaml", items, "# 아젠다 — '지표 관리' 화면에서 저장됨. 직접 수정해도 됩니다.")


def validate_metrics(items: list[dict]) -> list[str]:
    errs, seen = [], set()
    for i, m in enumerate(items, 1):
        if not m.get("key"):
            errs.append(f"{i}번째 지표: key 가 비어 있습니다.")
        elif m["key"] in seen:
            errs.append(f"key 중복: {m['key']}")
        seen.add(m.get("key"))
        if not m.get("formula") or not m.get("source"):
            errs.append(f"{m.get('key')}: formula/source 는 필수입니다.")
        if m.get("direction") not in ("up", "down"):
            errs.append(f"{m.get('key')}: direction 은 up 또는 down 이어야 합니다.")
    return errs
