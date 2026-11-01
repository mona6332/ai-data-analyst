import streamlit as st

from analyst.ui import get_sdb, key_warning, render_report, run_with_status

sdb = get_sdb()
agendas = sdb.agendas()
st.session_state.setdefault("chat", [])  # [{"role", "content", "charts", "sql_log", "report_id"}]

st.title("탐색 분석")
st.caption("상황 파악에서 본 특이사항을 대화로 파고듭니다. 분석이 끝나면 리포트가 분석 아카이브에 자동 저장됩니다. "
           "이어서 질문하면 직전 분석을 바탕으로 더 깊게 봅니다.")
key_warning()

left, right = st.columns([1, 3])
request = None
with left:
    st.markdown("**등록된 아젠다**")
    for a in agendas:
        with st.container(border=True):
            st.markdown(f'**{a["title"]}**  \n{a["question"]}')
            st.caption(" → ".join(s["name"] for s in a["steps"]))
            if st.button("이 아젠다로 시작", key="ag_" + a["id"], width="stretch"):
                request = (a["question"], a["id"], False)
    st.markdown("**문제 상황으로 시작**")
    for a in agendas:
        for sidx, text in enumerate(a.get("starters", [])):
            if st.button(text, key=f'st_{a["id"]}_{sidx}', width="stretch"):
                request = (text, a["id"], False)
    if st.session_state.chat and st.button("새 대화", type="secondary", width="stretch"):
        st.session_state.chat = []
        st.rerun()

with right:
    if not st.session_state.chat:
        st.info("왼쪽에서 아젠다나 문제 상황을 고르거나, 아래에 지금 상황을 적어 대화를 시작하세요.")
    for h in st.session_state.chat:
        with st.chat_message(h["role"]):
            if h["role"] == "user":
                st.markdown(h["content"])
            else:
                render_report(h["content"], h.get("charts"), h.get("sql_log"), key=h.get("report_id", ""))

    prefill = st.session_state.pop("explore_prefill", None)
    if prefill:
        request = (prefill[0], prefill[1], False)
    typed = st.chat_input("지금 상황을 적어 주세요 (예: 광고비는 늘었는데 ROAS가 떨어졌다)")
    if typed:
        request = (typed, None, True)  # 직접 입력은 대화가 있으면 후속 질문으로 처리

    if request:
        question, agenda_id, followup = request
        prev = next((h["content"] for h in reversed(st.session_state.chat) if h["role"] == "assistant"), None)
        if not followup:
            st.session_state.chat = []
            prev = None
        st.session_state.chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            res = run_with_status(question, agenda_id, previous_report=prev)
            if res:
                render_report(res["report_md"], res["charts"], res["sql_log"], key=res["report_id"])
                st.session_state.chat.append({"role": "assistant", "content": res["report_md"], "charts": res["charts"],
                                              "sql_log": res["sql_log"], "report_id": res["report_id"]})
