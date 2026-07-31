from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureDecision:
    error_type: str
    retryable: bool


class FailurePolicy:
    """Classifies execution failures so the graph can react consistently."""

    TRANSIENT_MARKERS = (
        "timeout",
        "timed out",
        "temporarily",
        "temporary",
        "connection",
        "network",
        "read operation",
        "ssl",
        "eof",
        "protocol",
        "remote protocol",
        "server disconnected",
        "unavailable",
        "service unavailable",
        "503",
        "502",
    )
    CONFIG_MARKERS = (
        "api_key",
        "not configured",
        "unauthorized",
        "invalid api key",
        "401",
        "403",
    )
    QUOTA_MARKERS = (
        "insufficient_quota",
        "token-limit",
        "current quota",
        "billing details",
    )
    RATE_LIMIT_MARKERS = (
        "rate limit",
        "limit_requests",
        "too many requests",
        "429",
    )

    def classify_model_failure(self, error: str) -> FailureDecision:
        text = (error or "").lower()
        if any(marker in text for marker in self.QUOTA_MARKERS):
            return FailureDecision(
                "model_quota_exhausted",
                True,
            )
        if any(marker in text for marker in self.CONFIG_MARKERS):
            return FailureDecision(
                "model_configuration_error",
                False,
            )
        if any(marker in text for marker in self.RATE_LIMIT_MARKERS):
            return FailureDecision(
                "model_rate_limited",
                True,
            )
        if any(marker in text for marker in self.TRANSIENT_MARKERS):
            return FailureDecision(
                "transient_model_error",
                True,
            )
        return FailureDecision(
            "model_call_error",
            False,
        )

    def classify_parse_failure(self, error: str) -> FailureDecision:
        return FailureDecision(
            "model_output_parse_error",
            True,
        )
