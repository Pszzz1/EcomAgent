from backend.app.infrastructure.settings import load_settings

from .messages import ModelProvider
from .providers import DashScopeModelProvider


def build_model_provider() -> ModelProvider:
    settings = load_settings()
    if settings.llm_provider == "mock":
        raise ValueError(
            "LLM_PROVIDER=mock is not allowed in the application runtime. "
            "Inject MockModelProvider explicitly in tests instead."
        )
    if settings.llm_provider == "dashscope":
        return DashScopeModelProvider(
            api_key=settings.dashscope_api_key,
            default_model=settings.llm_work_model,
            base_url=settings.dashscope_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
            enable_thinking=settings.llm_enable_thinking,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
