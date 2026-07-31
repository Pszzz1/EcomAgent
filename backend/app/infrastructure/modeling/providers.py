import time
from typing import Any, Dict

import httpx

from .messages import ModelCallResult, ModelMessage


class DashScopeModelProvider:
    provider_name = "dashscope"

    def __init__(
        self,
        api_key: str = "",
        default_model: str = "deepseek-v3",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout_seconds: float = 30.0,
        enable_thinking: bool = False,
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.enable_thinking = enable_thinking

    def complete(
        self,
        messages: list[ModelMessage],
        model: str | None = None,
        response_format: Dict[str, Any] | None = None,
    ) -> ModelCallResult:
        actual_model = model or self.default_model
        if not self.api_key:
            return ModelCallResult(
                provider=self.provider_name,
                model=actual_model,
                status="failed",
                error="DASHSCOPE_API_KEY is not configured.",
            )

        started = time.perf_counter()
        try:
            payload: Dict[str, Any] = {
                "model": actual_model,
                "messages": [{"role": item.role, "content": item.content} for item in messages],
                "temperature": 0,
            }
            if actual_model.lower().startswith("qwen3"):
                payload["enable_thinking"] = self.enable_thinking
            if response_format:
                payload["response_format"] = response_format
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            usage = data.get("usage", {})
            return ModelCallResult(
                provider=self.provider_name,
                model=actual_model,
                status="ok",
                content=str(message.get("content", "")),
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                latency_ms=int((time.perf_counter() - started) * 1000),
                raw={**data, "_response_meta": _response_meta(response)},
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            meta = _response_meta(response)
            detail = response.text[:1000]
            return ModelCallResult(
                provider=self.provider_name,
                model=actual_model,
                status="failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"HTTP {response.status_code}: {detail}",
                raw={"http_status": response.status_code, "response_body": detail, **meta},
            )
        except Exception as exc:
            return ModelCallResult(
                provider=self.provider_name,
                model=actual_model,
                status="failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )

def _response_meta(response: httpx.Response) -> Dict[str, Any]:
    retry_after = response.headers.get("retry-after", "")
    try:
        retry_after_seconds = float(retry_after) if retry_after else 0.0
    except ValueError:
        retry_after_seconds = 0.0
    return {
        "retry_after_seconds": retry_after_seconds,
        "request_id": response.headers.get("x-request-id", ""),
        "rate_limit_remaining": response.headers.get("x-ratelimit-remaining", ""),
        "rate_limit_reset": response.headers.get("x-ratelimit-reset", ""),
    }
