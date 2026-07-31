from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import urlparse

import httpx


@dataclass
class ImageGenerationResult:
    ok: bool
    model: str
    content: bytes = b""
    mime_type: str = "image/png"
    latency_ms: int = 0
    request_id: str = ""
    error: str = ""


class DashScopeImageGenerator:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(self, *, source_image: str, prompt: str, size: str) -> ImageGenerationResult:
        started = time.perf_counter()
        if not self.api_key or not self.model:
            return ImageGenerationResult(
                ok=False,
                model=self.model,
                error="Image generation model is not configured.",
            )
        try:
            response = httpx.post(
                f"{self.base_url}/services/aigc/multimodal-generation/generation",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": {
                        "messages": [
                            {
                                "role": "user",
                                "content": [{"image": source_image}, {"text": prompt}],
                            }
                        ]
                    },
                    "parameters": {"n": 1, "size": size},
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            image_url = _image_url(payload)
            parsed = urlparse(image_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not parsed.hostname.endswith(".aliyuncs.com")
            ):
                raise ValueError("Image model returned an untrusted result URL.")
            image_response = httpx.get(image_url, timeout=self.timeout_seconds)
            image_response.raise_for_status()
            return ImageGenerationResult(
                ok=True,
                model=self.model,
                content=image_response.content,
                latency_ms=int((time.perf_counter() - started) * 1000),
                request_id=str(payload.get("request_id", "")),
            )
        except Exception as exc:
            return ImageGenerationResult(
                ok=False,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )


def _image_url(payload: dict[str, Any]) -> str:
    choices = payload.get("output", {}).get("choices", [])
    content = choices[0].get("message", {}).get("content", []) if choices else []
    image_url = next(
        (
            str(item.get("image", ""))
            for item in content
            if isinstance(item, dict) and item.get("image")
        ),
        "",
    )
    if not image_url:
        raise ValueError("Image model response did not contain an image URL.")
    return image_url
