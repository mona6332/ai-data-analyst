# AI Data Analyst Agent (v1)

분석 허브의 **탐색 분석 · AI** 화면 뒤에서 동작하는 분석 에이전트입니다.
문제 상황(자연어)이나 등록된 아젠다를 받으면 SQL을 만들어 실행하고, Python으로 추가 분석한 뒤, 인사이트 리포트를 서비스 DB에 저장합니다.

## 아키텍처

```
[원천] 카페24 주문·회원 / 웹트래킹 / ERP 상품 / 프로모션 / 광고비
   ↓  (지금은 scripts/build_mock_db.py 가 임시 DB로 생성)
[DW]  SILVER(silver_*) → GOLD 표준 뷰(gold_*) → G1 팩트·디멘션(g1_*) + 도메인(domain_*)
   ↓  AI는 GOLD·G1·도메인에만 읽기 전용 SQL 실행
[에이전트 · LangGraph]
   prepare_context → analyst ⇄ tools → write_report → judge(품질 판정, 미달 시 재작성) → save_report
       │ 스키마·지표사전·아젠다 주입     │ list_tables / describe_table / lookup_metrics
       │                                │ run_sql (읽기 전용) / python_analysis (pandas)
   ↓
[서비스 DB] 지표 사전 · 아젠다 · 분석 리포트(분석 아카이브)
```

## 어디에 무엇이 있나

| 하고 싶은 것 | 파일 |
|---|---|
| **API 키 넣기** | `.env` (`.env.example`을 복사해 만들고 `OPENAI_API_KEY=` 뒤에 붙여넣기) |
| **지표 수정** | `config/metrics.yaml` 또는 화면의 **지표·아젠다 관리** |
| **아젠다 수정** | `config/agendas.yaml` 또는 화면의 **지표·아젠다 관리** |
| LLM 모델 바꾸기 | `.env`의 `LLM_PROVIDER`, `LLM_MODEL` |
| AI 지시문(프롬프트) 고치기 | `analyst/prompts.py` |
| 화면 고치기 | `app.py`(메뉴), `ui/dashboard.py`, `ui/explore.py`, `ui/auto_report.py`, `ui/archive.py`, `ui/metric_admin.py` |
| 특이사항 감지 규칙 | `analyst/insights.py` (임계치는 metrics.yaml의 `alert_pct`) |
| 에이전트 흐름 | `analyst/graph.py` (LangGraph), `analyst/tools.py` (도구 6종) |
| SQL 안전장치 | `analyst/sql_guard.py` |
| **리포트 품질 판정 기준** | `config/judge.yaml` (판정 로직 `analyst/judge.py`, 판사 모델 `.env`의 `JUDGE_MODEL`) |
| 저장된 리포트 재채점 | `scripts/judge_reports.py` |
| **배포 전 품질 평가** | 질문 세트 `eval/questions.yaml`, 실행 `scripts/eval.py`, 로직 `analyst/evaluate.py` |
| 회사 DB 연결 | `analyst/connectors/` |
| 임시 DB 다시 만들기 | `scripts/build_mock_db.py` |
| 자동 리포트 예약 실행 | `scripts/run_auto_report.py` |
| Claude Code 규칙 | `CLAUDE.md`, `.claude/settings.json` (`.env` 읽기 차단) |

## 화면 구성 (분석 허브와 동일한 메뉴)
- **대시보드**: 핵심 지표 13종의 최근 7일/28일 비교, 일별 추이, 특이사항 자동 감지 → 버튼 한 번으로 탐색 분석으로 넘김
- **자동 리포트**: 아젠다를 정해진 순서로 실행해 정기 리포트 생성
- **지표·아젠다 관리**: 지표 표 편집, 계산식 검증, 아젠다 YAML 편집
- **탐색 분석 (AI)**: 아젠다·문제 상황·직접 입력으로 분석, 진행 상황 표시, 리포트+차트+SQL, 이어서 질문하기
- **분석 아카이브**: 모든 리포트 검색·열람·삭제

## 도구 (Tool Calling)
`list_tables`, `describe_table`, `lookup_metrics`, `run_sql`(읽기 전용), `python_analysis`(pandas), `make_chart`

## 임시 DB에 심어둔 특이사항 (데모 검증용)
1. 최근 3주 META 광고비 약 +55%, ROAS 4.2 → 3.0 하락 → **광고 효율 점검**
2. '린넨 오버핏 셔츠'(P007) 10월 반품률 4% → 36% → **상품 이슈 점검**
3. 신규 고객은 늘지만 M+1 재구매율 43% → 7% → **고객 유지 점검**
4. 추석 세일(9/21~28) 직후 매출 급감 → **프로모션 사후 분석**

## 실행 (VS Code)
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # (Windows: copy .env.example .env) OPENAI_API_KEY 입력 (기본 LLM: OpenAI gpt-5)
python scripts/build_mock_db.py
python -m pytest -q         # LLM 없이 도는 테스트 31개

streamlit run app.py        # 분석 허브 화면 (http://localhost:8501)

python -m analyst.cli --list-agendas
python -m analyst.cli "광고비는 늘었는데 ROAS가 떨어졌다" --agenda ad_efficiency --show-sql
uvicorn analyst.api:app --reload    # http://127.0.0.1:8000/docs
```
VS Code의 실행/디버그 탭에 `분석 허브 화면(Streamlit)`, `임시 DB 생성`, `분석 실행`, `API 서버` 구성이 들어 있습니다.
서비스 흐름도와 단계별 인프라 계획은 `docs/service-flow-and-infra.md` 참고.

## 리포트 품질 판정 (LLM judge)
분석 에이전트와 분리된 판사 LLM이 리포트를 채점합니다. 기준과 통과 점수는 `config/judge.yaml`에서 고칩니다.
1. **숫자 대조 (코드)**: 리포트 속 숫자가 실제 조회·분석 결과에 있는지 확인 (결과값, %, 증감률, 차이까지 허용)
2. **LLM 채점**: 근거성 · 지표 정의 준수 · 질문 적합성 · 원인 구분 · 액션 구체성을 1~5점으로 채점, 가중 평균은 코드가 계산
3. 기준 미달이면 판사 의견을 붙여 리포트를 다시 쓰게 함 (`max_revisions`회까지). 판정 결과는 리포트와 함께 저장되어 탐색 분석·아카이브 화면에 표시
- 판사는 분석과 다른 모델을 쓰는 게 좋습니다: `.env`에 `JUDGE_MODEL=...` (비우면 같은 모델). 끄려면 `JUDGE_ENABLED=false`
- 프롬프트를 바꾼 뒤 품질 비교: `python scripts/judge_reports.py --limit 20` (저장된 리포트의 SQL을 다시 실행해 근거를 만들고 재채점)

## 배포 전 품질 평가 (평가 세트)
리포트 1건을 저장 전에 판정하는 것과 별개로, **프롬프트·모델·코드를 바꾼 버전이 전체적으로 나아졌는지**를 배포 전에 측정합니다.
- 질문 세트와 통과 기준: `eval/questions.yaml` (질문, 아젠다, 리포트에 꼭 나와야 할 핵심 단어, 평균·질문별·기준별 최소 점수)
- 실행: `python scripts/eval.py` → 질문마다 에이전트 실행 + 판정, 결과를 `eval/results/<시각>.md/.json`에 저장. 기준 미달이면 종료 코드 1
- 이전 결과와 비교: `python scripts/eval.py --baseline eval/results/<이전>.json`
- GitHub Actions: `tests`(PR마다 LLM 없는 테스트, 무료), `quality-eval`(수동 실행 또는 PR에 `run-eval` 라벨, LLM 비용 발생). 저장소 Secrets에 `OPENAI_API_KEY`를 직접 등록해야 동작

## 회사 DW로 바꿀 때
1. `analyst/connectors/`에 회사 DB용 커넥터 추가 (읽기 전용 계정 필수), `dialect`를 sqlglot 방언명으로 지정
2. `.env`의 `WAREHOUSE_CONNECTOR`, `WAREHOUSE_DSN`, `ALLOWED_TABLE_PREFIXES`(또는 스키마) 변경
3. 지표 사전을 회사 표준 정의로 교체

## 다음 단계 후보 (v2)
- 대화형 멀티턴(체크포인터) + 대시보드 특이사항에서 바로 시작
- Python 실행을 컨테이너 샌드박스로 이전, 사용자별 권한·감사 로그
- 질문↔검증된 SQL 예시 축적(few-shot)으로 정확도 개선

## Claude Code로 이어서 작업하기
이 폴더를 VS Code로 열고 터미널에서 `claude` 를 실행하면 됩니다. `CLAUDE.md`에 작업 규칙이 있고,
`.claude/settings.json`이 `.env` 읽기를 막아 API 키가 Claude에게 보이지 않습니다.
