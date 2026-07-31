from pathlib import Path

import pytest

from backend.app.infrastructure.modeling.factory import build_model_provider
from backend.app.infrastructure.modeling.messages import ModelMessage
from backend.app.infrastructure.modeling.providers import DashScopeModelProvider
from backend.app.infrastructure.settings import AppSettings, load_dotenv


def test_load_dotenv_uses_file_as_runtime_authority(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_WORK_MODEL=model-from-file\n", encoding="utf-8")
    monkeypatch.setenv("LLM_WORK_MODEL", "stale-model")

    load_dotenv(env_file)

    assert __import__("os").environ["LLM_WORK_MODEL"] == "model-from-file"


def test_load_settings_reads_model_roles(tmp_path: Path, monkeypatch) -> None:
    import backend.app.infrastructure.settings as settings_module

    (tmp_path / ".env").write_text(
        "LLM_WORK_MODEL=work-model\n"
        "LLM_REVIEW_MODEL=review-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "BASE_DIR", tmp_path)

    settings = settings_module.load_settings()

    assert settings.llm_work_model == "work-model"
    assert settings.llm_review_model == "review-model"


def test_runtime_factory_rejects_mock_provider(monkeypatch) -> None:
    import backend.app.infrastructure.modeling.factory as factory

    monkeypatch.setattr(factory, "load_settings", lambda: AppSettings(llm_provider="mock"))
    with pytest.raises(ValueError, match="not allowed"):
        build_model_provider()


def test_qwen3_structured_call_disables_thinking_by_default(monkeypatch) -> None:
    captured = {}

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("backend.app.infrastructure.modeling.providers.httpx.post", fake_post)
    provider = DashScopeModelProvider(api_key="test", default_model="qwen3-max")
    provider.complete([ModelMessage(role="user", content="{}")])

    assert captured["enable_thinking"] is False
