import streamlit as st

from analyst.ui import get_sdb, render_report

sdb = get_sdb()
agendas = {a["id"]: a["title"] for a in sdb.agendas()}
st.title("분석 아카이브")
st.caption("탐색 분석과 자동 리포트의 결과가 모두 쌓입니다. 실행한 SQL까지 함께 남아 있어 검토·재현이 가능합니다.")

reports = sdb.reports(limit=200)
kw = st.text_input("검색", placeholder="질문 또는 리포트 내용으로 검색")
if kw:
    reports = [r for r in reports if kw in (r["question"] or "") or kw in (r["report_md"] or "")]
st.caption(f"{len(reports)}건")
for r in reports:
    title = (r["report_md"] or "").splitlines()[0].lstrip("# ") if r["report_md"] else "(내용 없음)"
    label = "자동" if r["kind"] == "auto" else "탐색"
    with st.expander(f'[{label}] {r["created_at"]} · {title}'):
        st.caption(f'질문: {r["question"]} · 아젠다: {agendas.get(r["agenda_id"], "자유 질문")}')
        full = sdb.report(r["report_id"])
        render_report(full["report_md"], full["charts"], full["sql_log"], key=r["report_id"])
        if st.button("삭제", key="del_" + r["report_id"]):
            sdb.delete_report(r["report_id"])
            st.rerun()
