from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    # 분석 대상 DB (GOLD·G1·도메인). connector 이름 + DSN
    warehouse_connector: str = os.getenv("WAREHOUSE_CONNECTOR", "sqlite")
    warehouse_dsn: str = os.getenv("WAREHOUSE_DSN", str(ROOT / "data" / "warehouse.db"))
    # 서비스 DB (지표 사전, 아젠다, 분석 리포트 기록)
    service_db_path: str = os.getenv("SERVICE_DB_PATH", str(ROOT / "data" / "service.db"))
    # AI가 읽을 수 있는 테이블 접두사 (아키텍처: AI는 GOLD·G1·도메인에만 SQL 실행)
    allowed_table_prefixes: tuple[str, ...] = field(
        default_factory=lambda: tuple(os.getenv("ALLOWED_TABLE_PREFIXES", "gold_,g1_,domain_").split(","))
    )
    max_rows: int = int(os.getenv("SQL_MAX_ROWS", "500"))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "12"))
    # LLM
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")  # openai | anthropic
    llm_model: str = os.getenv("LLM_MODEL", "gpt-5")


settings = Settings()
