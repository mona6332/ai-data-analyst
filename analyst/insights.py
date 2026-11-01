"""대시보드용 지표 계산과 특이사항 자동 감지 (LLM 없이 규칙 기반).

지표 정의(config/metrics.yaml)의 formula/source/direction/alert_pct 를 그대로 사용하므로
지표를 추가·수정하면 대시보드와 감지 규칙에 자동 반영됩니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .connectors import BaseConnector

# 소스 테이블별: 날짜 컬럼, 쪼개 볼 차원, 세그먼트 크기(가중치) 계산식
SOURCES = {
    "g1_fact_sales_daily": {"date": "date", "dims": {"channel": "채널", "category": "카테고리", "product_id": "상품"},
                            "weight": "SUM(units)"},
    "g1_fact_marketing_daily": {"date": "date", "dims": {"channel": "채널", "campaign_id": "캠페인"},
                                "weight": "SUM(spend)"},
}
SUM_LIKE = ("SUM(",)  # 합계형 지표는 기간 길이로 나눠 일평균으로 비교


@dataclass
class Window:
    cur_start: str
    cur_end: str
    base_start: str
    base_end: str
    days: int


def data_date(conn: BaseConnector) -> date:
    return date.fromisoformat(str(conn.execute("SELECT MAX(date) FROM g1_fact_sales_daily", 1).df.iloc[0, 0]))


def make_window(end: date, days: int) -> Window:
    cs = end - timedelta(days=days - 1)
    be = cs - timedelta(days=1)
    bs = be - timedelta(days=days - 1)
    return Window(str(cs), str(end), str(bs), str(be), days)


def _is_sum(formula: str) -> bool:
    f = formula.replace(" ", "").upper()
    return f.startswith("SUM(") and "/" not in f


def _period_sql(m: dict, w: Window, dim: str | None = None) -> str:
    src = SOURCES[m["source_table"]]
    d = src["date"]
    sel_dim = f"{dim} AS segment, " if dim else ""
    grp = "GROUP BY 1, 2" if dim else "GROUP BY 1"
    return f"""
    SELECT {sel_dim}CASE WHEN {d} >= '{w.cur_start}' THEN 'cur' ELSE 'base' END AS period,
           {m["formula_sql"]} AS value, {src["weight"]} AS weight
    FROM {m["source_table"]}
    WHERE {d} BETWEEN '{w.base_start}' AND '{w.cur_end}'
    {grp}"""


def _change(cur, base, direction: str) -> tuple[float | None, float | None]:
    """변화율(%)과 '나빠진 정도'(%, 양수면 악화)"""
    if cur is None or base in (None, 0) or pd.isna(cur) or pd.isna(base):
        return None, None
    pct = (cur - base) / abs(base) * 100
    return pct, (-pct if direction == "up" else pct)


def kpis(conn: BaseConnector, metrics: list[dict], w: Window) -> list[dict]:
    out = []
    for m in metrics:
        if m["source_table"] not in SOURCES:
            continue
        df = conn.execute(_period_sql(m, w), 10).df.set_index("period")
        cur, base = df["value"].get("cur"), df["value"].get("base")
        pct, worse = _change(cur, base, m["direction"])
        out.append({**m, "cur": cur, "base": base, "pct": pct, "worse": worse})
    return out


def daily_trend(conn: BaseConnector, m: dict, start: str, end: str) -> pd.DataFrame:
    src = SOURCES[m["source_table"]]
    return conn.execute(f"""SELECT {src["date"]} AS date, {m["formula_sql"]} AS value FROM {m["source_table"]}
                            WHERE {src["date"]} BETWEEN '{start}' AND '{end}' GROUP BY 1 ORDER BY 1""", 1000).df


def _cohort_alert(conn: BaseConnector, m: dict) -> list[dict]:
    df = conn.execute(f"""SELECT cohort_month, {m["formula_sql"]} AS value FROM g1_fact_customer_cohort
                          GROUP BY 1 ORDER BY 1""", 100).df.dropna()
    if len(df) < 2:
        return []
    cur, base = df.iloc[-1], df.iloc[-2]
    pct, worse = _change(cur.value, base.value, m["direction"])
    if worse is None or worse < (m["alert_pct"] or 0):
        return []
    return [{"metric": m, "segment_type": "코호트", "segment": f'{cur.cohort_month} 첫 구매 고객',
             "cur": cur.value, "base": base.value, "pct": pct, "worse": worse, "share": 1.0,
             "compare": f"{base.cohort_month} 코호트 대비"}]


def detect_anomalies(conn: BaseConnector, metrics: list[dict], w: Window, min_share: float = 0.02,
                     top_n: int = 10) -> list[dict]:
    found = []
    for m in metrics:
        if m.get("alert_pct") in (None, "") or pd.isna(m.get("alert_pct")):
            continue
        thr = float(m["alert_pct"])
        if m["source_table"] == "g1_fact_customer_cohort":
            found += _cohort_alert(conn, m)
            continue
        if m["source_table"] not in SOURCES:
            continue
        # 전체
        for k in kpis(conn, [m], w):
            if k["worse"] is not None and k["worse"] >= thr:
                found.append({"metric": m, "segment_type": "전체", "segment": "전체", "cur": k["cur"], "base": k["base"],
                              "pct": k["pct"], "worse": k["worse"], "share": 1.0, "compare": "직전 기간 대비"})
        # 세그먼트별
        for dim, dim_label in SOURCES[m["source_table"]]["dims"].items():
            df = conn.execute(_period_sql(m, w, dim), 5000).df
            p = df.pivot_table(index="segment", columns="period", values=["value", "weight"], aggfunc="sum")
            if ("value", "cur") not in p.columns or ("value", "base") not in p.columns:
                continue
            total_w = p[("weight", "cur")].sum()
            for seg, r in p.iterrows():
                share = (r[("weight", "cur")] or 0) / total_w if total_w else 0
                if share < min_share:
                    continue
                pct, worse = _change(r[("value", "cur")], r[("value", "base")], m["direction"])
                if worse is not None and worse >= thr:
                    found.append({"metric": m, "segment_type": dim_label, "segment": str(seg),
                                  "cur": r[("value", "cur")], "base": r[("value", "base")], "pct": pct,
                                  "worse": worse, "share": share, "compare": "직전 기간 대비"})
    # 심각도 = 악화 정도(최대 200%로 제한) × 비중(제곱근으로 완화)
    for f in found:
        f["score"] = min(f["worse"], 200) * (f["share"] ** 0.5)
    found.sort(key=lambda f: f["score"], reverse=True)
    # 같은 지표·같은 세그먼트 중복 제거
    seen, out = set(), []
    for f in found:
        k = (f["metric"]["metric_key"], f["segment"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out[:top_n]


def fmt(v, kind: str) -> str:
    if v is None or pd.isna(v):
        return "-"
    if kind == "krw":
        if abs(v) >= 1e8:
            return f"{v / 1e8:,.2f}억"
        return f"{v / 1e4:,.0f}만원" if abs(v) >= 1e6 else f"{v:,.0f}원"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "ratio":
        return f"{v:.2f}"
    return f"{v:,.0f}"


def anomaly_question(a: dict, product_names: dict[str, str] | None = None) -> str:
    m = a["metric"]
    seg = a["segment"]
    if a["segment_type"] == "상품" and product_names:
        seg = f"{product_names.get(seg, seg)}({seg})"
    where = "" if a["segment_type"] == "전체" else f"{a['segment_type']} '{seg}'의 "
    trend = "올랐다" if a["pct"] > 0 else "떨어졌다"
    return (f"{where}{m['name_ko']}이(가) {a['compare']} {abs(a['pct']):.0f}% {trend} "
            f"({fmt(a['base'], m['format'])} → {fmt(a['cur'], m['format'])}). 원인을 분석해줘.")
