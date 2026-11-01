import streamlit as st

from analyst.insights import anomaly_question, daily_trend, data_date, detect_anomalies, fmt, kpis, make_window
from analyst.ui import get_conn, get_sdb

conn, sdb = get_conn(), get_sdb()
metrics = sdb.metrics()
end = data_date(conn)

st.title("대시보드")
c1, c2 = st.columns([3, 1])
c1.caption(f"데이터 기준 {end} · 임시(예시) 데이터")
days = c2.segmented_control("비교 기간", [7, 28], default=7, format_func=lambda d: f"최근 {d}일 vs 직전 {d}일")
w = make_window(end, days or 7)

# ---- KPI
ks = kpis(conn, metrics, w)
groups = sorted({k["group_name"] for k in ks}, key=["매출", "상품", "고객", "마케팅"].index
                if all(g in ["매출", "상품", "고객", "마케팅"] for g in {k["group_name"] for k in ks}) else str)
for g in groups:
    st.markdown(f"##### {g}")
    row = [k for k in ks if k["group_name"] == g]
    cols = st.columns(max(len(row), 4))
    for col, k in zip(cols, row):
        delta = None if k["pct"] is None else f'{k["pct"]:+.1f}%'
        col.metric(k["name_ko"], fmt(k["cur"], k["format"]), delta,
                   delta_color="normal" if k["direction"] == "up" else "inverse",
                   help=f'{k["definition"]} · 직전 {fmt(k["base"], k["format"])}')

# ---- 추이
st.divider()
names = {m["name_ko"]: m for m in metrics if m["source_table"] in ("g1_fact_sales_daily", "g1_fact_marketing_daily")}
pick = st.selectbox("일별 추이", list(names), index=0)
trend = daily_trend(conn, names[pick], w.base_start, w.cur_end).set_index("date")
st.line_chart(trend, y="value", height=240)

# ---- 특이사항
st.divider()
st.subheader("특이사항")
st.caption("지표 사전의 alert_pct(악화 임계치) 기준으로 자동 감지합니다. 버튼을 누르면 탐색 분석으로 넘어가 AI가 원인을 파고듭니다.")
prod_names = dict(conn.execute("SELECT product_id, product_name FROM g1_dim_product", 1000).df.values)
agenda_by_metric = {}
for a in sdb.agendas():
    for mk in a.get("metrics", []):
        agenda_by_metric.setdefault(mk, a["id"])

alerts = detect_anomalies(conn, metrics, w)
if not alerts:
    st.success("임계치를 넘은 특이사항이 없습니다.")
for i, a in enumerate(alerts):
    q = anomaly_question(a, prod_names)
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        seg = "전체" if a["segment_type"] == "전체" else f"{a['segment_type']} · {prod_names.get(a['segment'], a['segment'])}"
        c1.markdown(f"**{seg}** — {a['metric']['name_ko']}  \n{q}")
        if c2.button("탐색 분석 →", key=f"go_{i}"):
            st.session_state["explore_prefill"] = (q, agenda_by_metric.get(a["metric"]["metric_key"]))
            st.switch_page("ui/explore.py")
