import pandas as pd
import streamlit as st
import yaml

from analyst.definitions import (CONFIG_DIR, METRIC_FIELDS, load_metrics, save_agendas, save_metrics,
                                 validate_metrics)
from analyst.insights import SOURCES
from analyst.ui import get_conn

conn = get_conn()
st.title("지표·아젠다 관리")
st.caption("정의 원본: `config/` 폴더의 metrics.yaml, agendas.yaml. 여기서 저장하거나 파일을 직접 고쳐도 됩니다. "
           "저장하면 대시보드·특이사항 감지·AI 프롬프트에 바로 반영됩니다.")

tab_m, tab_a = st.tabs(["지표 사전", "아젠다"])

with tab_m:
    df = pd.DataFrame(load_metrics(), columns=METRIC_FIELDS)
    edited = st.data_editor(
        df, num_rows="dynamic", width="stretch", hide_index=True, height=520,
        column_config={
            "key": st.column_config.TextColumn("key", required=True),
            "name": "이름", "group": st.column_config.SelectboxColumn("묶음", options=["매출", "상품", "고객", "마케팅"]),
            "definition": st.column_config.TextColumn("정의", width="medium"),
            "formula": st.column_config.TextColumn("계산식(SQL)", width="large"),
            "source": st.column_config.SelectboxColumn(
                "테이블", options=[t["name"] for t in conn.list_tables() if t["name"].startswith(("g1_", "gold_"))]),
            "direction": st.column_config.SelectboxColumn("좋은 방향", options=["up", "down"]),
            "alert_pct": st.column_config.NumberColumn("감지 임계치(%)", min_value=0, max_value=1000),
            "format": st.column_config.SelectboxColumn("표시", options=["krw", "int", "pct", "ratio"]),
        })
    c1, c2 = st.columns(2)
    if c1.button("계산식 검증", width="stretch"):
        for m in edited.to_dict("records"):
            try:
                v = conn.execute(f'SELECT {m["formula"]} AS v FROM {m["source"]}', 1).df.iloc[0, 0]
                st.write(f'✅ {m["key"]}: 전체 기간 값 = {v}')
            except Exception as e:
                st.write(f'❌ {m["key"]}: {e}')
    if c2.button("저장", type="primary", width="stretch"):
        items = [{k: (None if pd.isna(v) else v) for k, v in m.items()} for m in edited.to_dict("records")]
        errs = validate_metrics(items)
        if errs:
            st.error("\n".join(errs))
        else:
            save_metrics(items)
            st.success("저장했습니다.")
    st.caption("대시보드에 나오는 지표: 테이블이 " + ", ".join(SOURCES) + " 인 지표 (g1_fact_customer_cohort 는 특이사항 감지만)")

with tab_a:
    st.caption("아젠다는 단계(steps)가 들어 있어 YAML로 편집합니다. guide 는 AI에게 주는 단계별 지침입니다.")
    raw = (CONFIG_DIR / "agendas.yaml").read_text(encoding="utf-8")
    text = st.text_area("agendas.yaml", raw, height=600)
    if st.button("아젠다 저장", type="primary"):
        try:
            items = yaml.safe_load(text)
            assert isinstance(items, list) and all("id" in a and "title" in a and "steps" in a for a in items), \
                "각 아젠다에 id, title, steps 가 필요합니다."
            save_agendas(items)
            st.success("저장했습니다.")
        except Exception as e:
            st.error(f"저장 실패: {e}")
