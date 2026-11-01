"""SQL 안전성 검사: 단일 SELECT만, 허용된 테이블(GOLD·G1·도메인)만, 행 수 제한."""
from __future__ import annotations

import sqlglot
from sqlglot import exp


class UnsafeSQLError(ValueError):
    pass


_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
              exp.Merge, exp.TruncateTable, exp.Pragma, exp.Attach, exp.Detach)


def validate_sql(sql: str, dialect: str, allowed_prefixes: tuple[str, ...], max_rows: int) -> str:
    sql = sql.strip().rstrip(";")
    try:
        stmts = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"SQL 파싱 실패: {e}") from e
    if len(stmts) != 1:
        raise UnsafeSQLError("한 번에 하나의 SQL 문만 실행할 수 있습니다.")
    tree = stmts[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeSQLError("SELECT(조회) 문만 허용됩니다.")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeSQLError(f"허용되지 않는 구문: {type(node).__name__}")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if not name or name in cte_names:
            continue
        if not name.startswith(tuple(p.lower() for p in allowed_prefixes)):
            raise UnsafeSQLError(
                f"'{t.name}' 테이블은 조회할 수 없습니다. 허용 범위: {', '.join(p + '*' for p in allowed_prefixes)}"
            )
    # 바깥쪽 쿼리에 LIMIT 강제
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
    return tree.sql(dialect=dialect)
