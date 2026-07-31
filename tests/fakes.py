import json
from typing import Any

from backend.app.infrastructure.modeling.messages import ModelCallResult, ModelMessage


class QueueModelProvider:
    """Contract fake that never pretends to perform semantic reasoning."""

    provider_name = "queue"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[ModelMessage],
        model: str | None = None,
        response_format: dict | None = None,
    ) -> ModelCallResult:
        self.calls.append(
            {"messages": list(messages), "model": model or "", "response_format": response_format}
        )
        if not self.responses:
            raise AssertionError("QueueModelProvider received an unexpected model call.")
        response = self.responses.pop(0)
        if isinstance(response, ModelCallResult):
            response.model = model or response.model
            return response
        return ModelCallResult(
            provider=self.provider_name,
            model=model or "queue-model",
            status="ok",
            content=json.dumps(response, ensure_ascii=False),
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1,
        )
