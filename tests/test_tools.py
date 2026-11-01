from analyst.config import Settings
from analyst.connectors import get_connector
from analyst.service_db import ServiceDB
from analyst.tools import Workspace, build_tools


def _tools(tmp_path):
    cfg = Settings()
    ws = Workspace()
    tools = build_tools(get_connector("sqlite", cfg.warehouse_dsn), ServiceDB(str(tmp_path / "s.db")), cfg, ws)
    return {t.name: t for t in tools}, ws


def test_list_tables_hides_silver(tmp_path):
    t, _ = _tools(tmp_path)
    out = t["list_tables"].invoke({})
    assert "g1_fact_sales_daily" in out and "silver_" not in out


def test_sql_then_python(tmp_path):
    t, ws = _tools(tmp_path)
    out = t["run_sql"].invoke({"sql": "SELECT channel, SUM(spend) spend, SUM(attributed_revenue) rev "
                                      "FROM g1_fact_marketing_daily GROUP BY 1", "purpose": "채널별 ROAS"})
    assert out.startswith("[r1]")
    py = t["python_analysis"].invoke({"code": "r1['roas']=r1.rev/r1.spend\nprint(r1.sort_values('roas').iloc[0].channel)"})
    assert py.strip() == "META"


def test_sql_blocked(tmp_path):
    t, ws = _tools(tmp_path)
    assert t["run_sql"].invoke({"sql": "SELECT * FROM silver_orders", "purpose": "x"}).startswith("BLOCKED")


def test_python_sandbox_blocks_import(tmp_path):
    t, _ = _tools(tmp_path)
    assert "ERROR" in t["python_analysis"].invoke({"code": "import os\nprint(os.listdir('.'))"})
