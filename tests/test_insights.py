from analyst.config import Settings
from analyst.connectors import get_connector
from analyst.insights import data_date, detect_anomalies, kpis, make_window
from analyst.service_db import ServiceDB


def test_anomalies_find_planted_issues(tmp_path):
    conn = get_connector("sqlite", Settings().warehouse_dsn)
    metrics = ServiceDB(str(tmp_path / "s.db")).metrics()
    w = make_window(data_date(conn), 28)
    assert any(k["metric_key"] == "roas" and k["pct"] < 0 for k in kpis(conn, metrics, w))
    found = {(a["metric"]["metric_key"], a["segment"]) for a in detect_anomalies(conn, metrics, w, top_n=30)}
    assert ("return_rate", "P007") in found       # 린넨 셔츠 반품 급증
    assert ("roas", "META") in found               # META 효율 하락
    assert any(k == "m1_retention" for k, _ in found)  # 재구매율 하락
