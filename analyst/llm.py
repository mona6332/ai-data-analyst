from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from .config import Settings


def get_llm(cfg: Settings, model: str | None = None) -> BaseChatModel:
    """LLM 공급자 교체 지점. 사내 게이트웨이/다른 공급자는 여기에 추가합니다."""
    model = model or cfg.llm_model
    if cfg.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=0, max_tokens=4096)
    if cfg.llm_provider == "openai":
        from langchain_openai import ChatOpenAI
        # gpt-5 계열 추론 모델은 temperature 지정을 지원하지 않아 기본값 사용
        return ChatOpenAI(model=model)
    raise ValueError(f"지원하지 않는 LLM_PROVIDER: {cfg.llm_provider}")


def get_judge_llm(cfg: Settings) -> BaseChatModel:
    """리포트 판정용 LLM. JUDGE_MODEL 로 분석 모델과 다른 모델을 지정할 수 있습니다."""
    return get_llm(cfg, cfg.judge_model or None)
