from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Dict, List

from backend.app.infrastructure.modeling.messages import ModelCallResult, ModelMessage, ModelProvider

from .failure_policy import FailurePolicy
from .retry_policy import RetryPolicy


@dataclass
class ModelExecutionOutcome:
    ok: bool
    node_name: str
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    result: ModelCallResult | None = None
    parsed: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_type: str = ""
    result_type: str = "success"


@dataclass
class ModelHealthRegistry:
    unavailable_until: Dict[str, float] = field(default_factory=dict)

    def is_available(self, model: str, now: float) -> bool:
        return self.unavailable_until.get(model, 0.0) <= now

    def mark_unavailable(self, model: str, until: float) -> None:
        if model:
            self.unavailable_until[model] = until


class ModelExecutionRunner:
    def __init__(
        self,
        provider: ModelProvider,
        model: str = "",
        failure_policy: FailurePolicy | None = None,
        retry_policy: RetryPolicy | None = None,
        fallback_models: List[str] | None = None,
        health_registry: ModelHealthRegistry | None = None,
    ) -> None:
        self.provider = provider
        self.model = model.strip()
        self.failure_policy = failure_policy or FailurePolicy()
        self.retry_policy = retry_policy or RetryPolicy()
        self.models = list(
            dict.fromkeys(
                model_name.strip()
                for model_name in [self.model, *(fallback_models or [])]
                if model_name.strip()
            )
        )
        self.health_registry = health_registry or ModelHealthRegistry()

    def complete_structured(
        self,
        *,
        node_name: str,
        messages: list[ModelMessage],
        response_format: Dict[str, Any] | None,
        parser: Callable[[str], Any],
    ) -> ModelExecutionOutcome:
        attempts: List[Dict[str, Any]] = []
        last_result: ModelCallResult | None = None
        last_error = ""
        last_error_type = ""
        last_result_type = "success"
        request_messages = list(messages)
        network_failed_models: set[str] = set()
        selected_model = ""
        network_failures = 0
        parse_failures = 0

        for attempt_index in range(1, self.retry_policy.max_attempts + 1):
            if not selected_model:
                selected_model = self._select_model(network_failed_models)
            result = self.provider.complete(
                request_messages,
                model=selected_model or None,
                response_format=response_format,
            )
            last_result = result
            if result.status != "ok":
                if selected_model:
                    network_failed_models.add(selected_model)
                network_failures += 1
                decision = self.failure_policy.classify_model_failure(result.error)
                will_retry = self.retry_policy.should_retry(
                    failure_count=network_failures,
                    retryable=decision.retryable,
                    error_type=decision.error_type,
                ) and attempt_index < self.retry_policy.max_attempts
                attempts.append(
                    self._attempt_payload(
                        node_name=node_name,
                        attempt_index=attempt_index,
                        result=result,
                        error_type=decision.error_type,
                        retryable=decision.retryable,
                        will_retry=will_retry,
                        error=result.error,
                    )
                )
                last_error = result.error
                last_error_type = decision.error_type
                last_result_type = "technical_failure"
                if decision.error_type in {"model_rate_limited", "model_quota_exhausted"}:
                    self._mark_unavailable(selected_model, result.raw)
                if will_retry:
                    selected_model = ""
                    time.sleep(
                        self.retry_policy.delay_seconds(
                            attempt_index=attempt_index,
                            retry_after_seconds=_retry_after_seconds(result.raw),
                        )
                    )
                    continue
                break

            try:
                parsed = parser(result.content)
            except Exception as exc:
                parse_failures += 1
                decision = self.failure_policy.classify_parse_failure(str(exc))
                will_retry = self.retry_policy.should_retry(
                    failure_count=parse_failures,
                    retryable=decision.retryable,
                    error_type=decision.error_type,
                ) and attempt_index < self.retry_policy.max_attempts
                attempts.append(
                    self._attempt_payload(
                        node_name=node_name,
                        attempt_index=attempt_index,
                        result=result,
                        error_type=decision.error_type,
                        retryable=decision.retryable,
                        will_retry=will_retry,
                        error=str(exc),
                        response_preview=result.content[:2000],
                    )
                )
                last_error = str(exc)
                last_error_type = decision.error_type
                last_result_type = "contract_failure"
                if will_retry:
                    request_messages = [
                        *messages,
                        ModelMessage(role="assistant", content=result.content[:4000]),
                        ModelMessage(
                            role="user",
                            content=(
                                "上一份 JSON 未通过结构契约："
                                + str(exc)
                                + "。只修正错误指出的契约问题，不改变任务语义，重新返回完整 JSON。"
                            ),
                        ),
                    ]
                    continue
                break

            attempts.append(
                self._attempt_payload(
                    node_name=node_name,
                    attempt_index=attempt_index,
                    result=result,
                    error_type="",
                    retryable=False,
                    will_retry=False,
                    error="",
                )
            )
            return ModelExecutionOutcome(
                ok=True,
                node_name=node_name,
                attempts=attempts,
                result=result,
                parsed=parsed.to_dict() if hasattr(parsed, "to_dict") else dict(parsed),
            )

        return ModelExecutionOutcome(
            ok=False,
            node_name=node_name,
            attempts=attempts,
            result=last_result,
            error=last_error,
            error_type=last_error_type,
            result_type=last_result_type,
        )

    def _select_model(self, attempted_models: set[str]) -> str:
        if not self.models:
            return ""
        now = time.monotonic()
        available = [
            model
            for model in self.models
            if self.health_registry.is_available(model, now)
        ]
        for model in available:
            if model not in attempted_models:
                return model
        if available:
            return available[-1]
        return min(
            self.models,
            key=lambda model: self.health_registry.unavailable_until.get(model, 0.0),
        )

    def _mark_unavailable(self, model: str, raw: Dict[str, Any]) -> None:
        if not model:
            return
        cooldown = max(
            self.retry_policy.unavailable_model_cooldown_seconds,
            _retry_after_seconds(raw),
        )
        self.health_registry.mark_unavailable(model, time.monotonic() + cooldown)

    def _attempt_payload(
        self,
        *,
        node_name: str,
        attempt_index: int,
        result: ModelCallResult,
        error_type: str,
        retryable: bool,
        will_retry: bool,
        error: str,
        response_preview: str = "",
    ) -> Dict[str, Any]:
        payload = {
            "node_name": node_name,
            "attempt_index": attempt_index,
            "max_attempts": self.retry_policy.attempts_for(error_type) if error_type else 1,
            "provider": result.provider,
            "model": result.model,
            "status": "parse_failed" if error_type == "model_output_parse_error" else result.status,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": result.latency_ms,
            "error": error,
            "error_type": error_type,
            "retryable": retryable,
            "will_retry": will_retry,
        }
        if response_preview:
            payload["response_preview"] = response_preview
        return payload


def _retry_after_seconds(raw: Dict[str, Any]) -> float:
    try:
        return max(0.0, float(raw.get("retry_after_seconds", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0
