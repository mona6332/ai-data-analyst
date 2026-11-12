import streamlit as st

from analyst.ui import get_sdb, has_llm_key, key_warning, render_report, run_with_status

sdb = get_sdb()
st.title("자동 리포트")
st.caption("아젠다를 정해진 순서대로 실행해 정기 리포트를 만듭니다. 매주 자동 생성은 "
           "`python scripts/run_auto_report.py --agenda weekly_sales` 를 작업 스케줄러에 등록하면 됩니다.")
key_warning()

agendas = {a["id"]: a for a in sdb.agendas()}
c1, c2 = st.columns([3, 1])
aid = c1.selectbox("리포트 종류", list(agendas), format_func=lambda i: agendas[i]["title"])
if c2.button("지금 생성", type="primary", disabled=not has_llm_key(), width="stretch"):
    res = run_with_status(f'[자동 리포트] {agendas[aid]["title"]}: {agendas[aid]["question"]}', aid, kind="auto")
    if res:
        st.success("생성 완료 — 아래 목록에 추가되었습니다.")

st.divider()
reports = sdb.reports(kind="auto")
if not reports:
    st.info("아직 생성된 자동 리포트가 없습니다.")
for r in reports:
    title = (r["report_md"] or "").splitlines()[0].lstrip("# ") if r["report_md"] else r["question"]
    with st.expander(f'{r["created_at"]} · {agendas.get(r["agenda_id"], {}).get("title", "")} · {title}'):
        full = sdb.report(r["report_id"])
        render_report(full["report_md"], full["charts"], full["sql_log"], key=r["report_id"], judge=full.get("judge"))
