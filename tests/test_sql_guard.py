import pytest

from analyst.sql_guard import UnsafeSQLError, validate_sql

P = ("gold_", "g1_", "domain_")


def ok(sql):
    return validate_sql(sql, "sqlite", P, 100)


def test_select_gets_limit():
    assert "LIMIT 100" in ok("SELECT channel, SUM(spend) FROM g1_fact_marketing_daily GROUP BY 1")


def test_cte_allowed():
    ok("WITH t AS (SELECT * FROM g1_fact_sales_daily) SELECT COUNT(*) FROM t")


@pytest.mark.parametrize("sql", [
    "DELETE FROM g1_fact_sales_daily",
    "DROP TABLE g1_fact_sales_daily",
    "UPDATE g1_dim_product SET list_price = 0",
    "SELECT * FROM silver_orders",               # 원천 테이블 접근 차단
    "SELECT * FROM g1_dim_product; DROP TABLE x",  # 다중 문장 차단
    "PRAGMA table_info(g1_dim_product)",
    "ATTACH DATABASE 'x.db' AS x",
])
def test_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        ok(sql)
