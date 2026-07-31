from dataclasses import dataclass, field
from typing import Any, Dict, Protocol


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: Any


@dataclass
class ModelCallResult:
    provider: str
    model: str
    status: str
    content: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    error: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class ModelProvider(Protocol):
    provider_name: str

    def complete(
        self,
        messages: list[ModelMessage],
        model: str | None = None,
        response_format: Dict[str, Any] | None = None,
    ) -> ModelCallResult:
        ...
