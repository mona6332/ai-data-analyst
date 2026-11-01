"""Streamlit 각 화면이 API 키 없이도 에러 없이 렌더링되는지 확인."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("page", ["dashboard", "explore", "auto_report", "archive", "metric_admin"])
def test_page_renders(page, monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    at = AppTest.from_file(str(ROOT / "ui" / f"{page}.py")).run(timeout=60)
    assert not at.exception, at.exception
