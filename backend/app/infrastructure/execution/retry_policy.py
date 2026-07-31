from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    network_max_attempts: int = 3
    parse_max_attempts: int = 2
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    unavailable_model_cooldown_seconds: float = 120.0

    @property
    def max_attempts(self) -> int:
        return max(1, self.network_max_attempts + self.parse_max_attempts - 1)

    def attempts_for(self, error_type: str) -> int:
        if error_type == "model_output_parse_error":
            return self.parse_max_attempts
        return self.network_max_attempts

    def should_retry(self, *, failure_count: int, retryable: bool, error_type: str) -> bool:
        return retryable and failure_count < self.attempts_for(error_type)

    def delay_seconds(self, *, attempt_index: int, retry_after_seconds: float = 0.0) -> float:
        exponential = self.initial_delay_seconds * (2 ** max(0, attempt_index - 1))
        return max(retry_after_seconds, min(exponential, self.max_delay_seconds))
