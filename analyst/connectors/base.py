from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class QueryResult:
    df: pd.DataFrame
    truncated: bool


class BaseConnector(ABC):
    """모든 DB 커넥터의 공통 인터페이스.

    - dialect: sqlglot 방언 이름 (SQL 안전성 검사에 사용)
    - 구현체는 반드시 '읽기 전용' 연결을 사용해야 합니다 (예: 읽기 전용 계정/트랜잭션).
    """

    dialect: str = "sqlite"

    def __init__(self, dsn: str, **kwargs):
        self.dsn = dsn

    @abstractmethod
    def list_tables(self) -> list[dict]:
        """[{"name":..., "type": "table"|"view"}]"""

    @abstractmethod
    def describe_table(self, table: str) -> list[dict]:
        """[{"column":..., "type":...}]"""

    @abstractmethod
    def execute(self, sql: str, max_rows: int) -> QueryResult:
        """검증된 SELECT를 실행해 최대 max_rows 행을 반환합니다."""
