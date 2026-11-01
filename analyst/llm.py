from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from .config import Settings


def get_llm(cfg: Settings) -> BaseChatModel:
    """LLM 공급자 교체 지점. 사내 게이트웨이/다른 공급자는 여기에 추가합니다."""
    if cfg.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=cfg.llm_model, temperature=0, max_tokens=4096)
    if cfg.llm_provider == "openai":
        from langchain_openai import ChatOpenAI
        # gpt-5 계열 추론 모델은 temperature 지정을 지원하지 않아 기본값 사용
        return ChatOpenAI(model=cfg.llm_model)
    raise ValueError(f"지원하지 않는 LLM_PROVIDER: {cfg.llm_provider}")
