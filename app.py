"""분석 허브 v1 (Streamlit)

  streamlit run app.py
"""
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="분석 허브", layout="wide")

pages = {
    "상황 파악 · 무슨 일이 있었나": [
        st.Page("ui/dashboard.py", title="대시보드", default=True),
        st.Page("ui/auto_report.py", title="자동 리포트"),
        st.Page("ui/metric_admin.py", title="지표·아젠다 관리"),
    ],
    "문제 해결 · 왜 그랬고, 무엇을 할까": [
        st.Page("ui/explore.py", title="탐색 분석 (AI)"),
        st.Page("ui/archive.py", title="분석 아카이브"),
    ],
}
st.navigation(pages).run()
