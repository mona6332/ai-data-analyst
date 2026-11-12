"""분석 허브 화면(탐색 분석)에서 호출할 API.

  uvicorn analyst.api:app --reload
"""
from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

load_dotenv()

from .config import settings  # noqa: E402
from .graph import run_analysis  # noqa: E402
from .service_db import ServiceDB  # noqa: E402

app = FastAPI(title="AI Data Analyst Agent", version="0.1.0")


class AnalyzeRequest(BaseModel):
    question: str = ""
    agenda_id: str | None = None
    mode: str | None = None  # auto | single | multi


@app.get("/agendas")
def agendas():
    return ServiceDB(settings.service_db_path).agendas()


@app.get("/metrics")
def metrics():
    return ServiceDB(settings.service_db_path).metrics()


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    return run_analysis(req.question, req.agenda_id, mode=req.mode)


@app.get("/reports")
def reports():
    return ServiceDB(settings.service_db_path).reports()
