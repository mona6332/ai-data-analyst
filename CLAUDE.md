# AI Data Analyst Agent — Claude Code 작업 메모

## 규칙
- `.env`(API 키)는 절대 읽거나 출력하지 않는다. 설정 예시는 `.env.example` 만 참고한다.
- AI 에이전트의 SQL은 `gold_*`, `g1_*`, `domain_*` 만 읽기 전용으로 조회한다 (`analyst/sql_guard.py`). 이 원칙을 약화시키지 않는다.
- 지표·아젠다·에이전트 정의는 코드가 아니라 `config/metrics.yaml`, `config/agendas.yaml`, `config/agents.yaml` 에 둔다.
- 변경 후 `python -m pytest -q` 를 통과시킨다 (LLM 없이 도는 테스트).

## 구조
- `app.py` + `ui/*.py`: Streamlit 화면 (대시보드, 자동 리포트, 지표·아젠다 관리, 탐색 분석, 아카이브)
- `analyst/graph.py`: LangGraph 워크플로우 (분석 에이전트 병렬 → 리포트 작성 → 평가) / `analyst/tools.py`: Tool Calling 도구
- `config/agents.yaml`: 멀티 에이전트(정형·탐색·리포트 작성) 역할·지시문·모드 라우팅. 에이전트 동작 조정은 코드보다 이 파일에서
- `analyst/judge.py`: 리포트 품질 판정(LLM judge, 기준은 `config/judge.yaml`) / `scripts/judge_reports.py`: 저장 리포트 재채점
- `analyst/evaluate.py` + `scripts/eval.py` + `eval/questions.yaml`: 배포 전 품질 평가 (고정 질문 세트, 통과 기준 미달 시 실패)
- `analyst/insights.py`: 대시보드 KPI·특이사항 감지 (규칙 기반)
- `analyst/connectors/`: DB 커넥터 (회사 DW 연결 시 여기에 추가)
- `scripts/build_mock_db.py`: 임시 DB 생성
