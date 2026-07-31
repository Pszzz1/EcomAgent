import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[3]


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


@dataclass(frozen=True)
class AppSettings:
    app_name: str = "copy-risk-review-agent"
    environment: str = "local"
    llm_provider: str = "dashscope"
    llm_work_model: str = "deepseek-v3"
    llm_review_model: str = "deepseek-v3"
    llm_enable_thinking: bool = False
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_native_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    image_generation_model: str = ""
    llm_timeout_seconds: float = 30.0
    task_db_path: str = "data/release_tasks.sqlite3"
    image_asset_path: str = "data/assets"
    api_cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


def load_settings() -> AppSettings:
    load_dotenv()
    work_model = os.getenv("LLM_WORK_MODEL", "deepseek-v3")
    return AppSettings(
        environment=os.getenv("APP_ENV", "local"),
        llm_provider=os.getenv("LLM_PROVIDER", "dashscope"),
        llm_work_model=work_model,
        llm_review_model=os.getenv("LLM_REVIEW_MODEL", work_model),
        llm_enable_thinking=_env_bool("LLM_ENABLE_THINKING", default=False),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        dashscope_base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        dashscope_native_base_url=os.getenv(
            "DASHSCOPE_NATIVE_BASE_URL",
            "https://dashscope.aliyuncs.com/api/v1",
        ),
        image_generation_model=os.getenv("IMAGE_GENERATION_MODEL", ""),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        task_db_path=os.getenv("TASK_DB_PATH", "data/release_tasks.sqlite3"),
        image_asset_path=os.getenv("IMAGE_ASSET_PATH", "data/assets"),
        api_cors_origins=tuple(
            item.strip()
            for item in os.getenv(
                "API_CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if item.strip()
        ),
    )


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
