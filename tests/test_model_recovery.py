from backend.app.infrastructure.execution import ModelExecutionRunner, ModelHealthRegistry, RetryPolicy
from backend.app.infrastructure.modeling.messages import ModelCallResult, ModelMessage
from backend.app.infrastructure.modeling.structured_output.common import _loads
from tests.fakes import QueueModelProvider


def test_rate_limit_switches_to_configured_fallback_model(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.infrastructure.execution.model_executor.time.sleep", lambda _: None)
    provider = QueueModelProvider(
        [
            ModelCallResult(provider="queue", model="", status="failed", error="429 Too Many Requests"),
            {"ok": True},
        ]
    )
    runner = ModelExecutionRunner(
        provider,
        model="model-a",
        fallback_models=["model-b"],
        retry_policy=RetryPolicy(network_max_attempts=2),
    )

    outcome = runner.complete_structured(
        node_name="test",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )

    assert outcome.ok
    assert [call["model"] for call in provider.calls] == ["model-a", "model-b"]
    assert outcome.attempts[0]["error_type"] == "model_rate_limited"


def test_rate_limited_model_is_skipped_by_other_runners_during_cooldown(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.infrastructure.execution.model_executor.time.sleep", lambda _: None)
    provider = QueueModelProvider(
        [
            ModelCallResult(provider="queue", model="", status="failed", error="429 Too Many Requests"),
            {"ok": True},
            {"ok": True},
        ]
    )
    health_registry = ModelHealthRegistry()
    first_runner = ModelExecutionRunner(
        provider,
        model="model-a",
        fallback_models=["model-b"],
        retry_policy=RetryPolicy(network_max_attempts=2),
        health_registry=health_registry,
    )
    second_runner = ModelExecutionRunner(
        provider,
        model="model-a",
        fallback_models=["model-b"],
        retry_policy=RetryPolicy(network_max_attempts=2),
        health_registry=health_registry,
    )

    first = first_runner.complete_structured(
        node_name="first",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )
    second = second_runner.complete_structured(
        node_name="second",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )

    assert first.ok and second.ok
    assert [call["model"] for call in provider.calls] == ["model-a", "model-b", "model-b"]


def test_invalid_json_gets_one_feedback_repair(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.infrastructure.execution.model_executor.time.sleep", lambda _: None)
    provider = QueueModelProvider(
        [
            ModelCallResult(provider="queue", model="", status="ok", content="not-json"),
            {"ok": True},
        ]
    )
    runner = ModelExecutionRunner(provider, model="model-a", fallback_models=["model-b"])

    outcome = runner.complete_structured(
        node_name="test",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )

    assert outcome.ok
    assert len(provider.calls) == 2
    assert [call["model"] for call in provider.calls] == ["model-a", "model-a"]
    assert "未通过结构契约" in provider.calls[1]["messages"][-1].content


def test_parse_repair_remains_available_after_network_fallback(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.infrastructure.execution.model_executor.time.sleep", lambda _: None)
    provider = QueueModelProvider(
        [
            ModelCallResult(provider="queue", model="", status="failed", error="429 Too Many Requests"),
            ModelCallResult(provider="queue", model="", status="ok", content="not-json"),
            {"ok": True},
        ]
    )
    runner = ModelExecutionRunner(
        provider,
        model="model-a",
        fallback_models=["model-b", "model-c"],
    )

    outcome = runner.complete_structured(
        node_name="test",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )

    assert outcome.ok
    assert [call["model"] for call in provider.calls] == ["model-a", "model-b", "model-b"]
    assert outcome.attempts[1]["error_type"] == "model_output_parse_error"
    assert outcome.attempts[1]["will_retry"] is True


def test_parse_repair_budget_is_not_consumed_by_prior_network_failures(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.infrastructure.execution.model_executor.time.sleep", lambda _: None)
    provider = QueueModelProvider(
        [
            ModelCallResult(provider="queue", model="", status="failed", error="429 Too Many Requests"),
            ModelCallResult(provider="queue", model="", status="failed", error="429 Too Many Requests"),
            ModelCallResult(provider="queue", model="", status="ok", content="not-json"),
            {"ok": True},
        ]
    )
    runner = ModelExecutionRunner(
        provider,
        model="model-a",
        fallback_models=["model-b", "model-c"],
        retry_policy=RetryPolicy(
            network_max_attempts=3,
            parse_max_attempts=2,
            initial_delay_seconds=0,
        ),
    )

    outcome = runner.complete_structured(
        node_name="test",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )

    assert outcome.ok
    assert len(provider.calls) == 4
    assert outcome.attempts[2]["error_type"] == "model_output_parse_error"
    assert outcome.attempts[2]["will_retry"] is True


def test_exhausted_rate_limit_reports_the_final_failed_attempt(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.infrastructure.execution.model_executor.time.sleep", lambda _: None)
    provider = QueueModelProvider(
        [ModelCallResult(provider="queue", model="", status="failed", error="429 Too Many Requests")]
    )
    runner = ModelExecutionRunner(
        provider,
        model="model-a",
        retry_policy=RetryPolicy(network_max_attempts=1),
    )

    outcome = runner.complete_structured(
        node_name="test",
        messages=[ModelMessage(role="user", content="{}")],
        response_format={"type": "json_object"},
        parser=_loads,
    )

    assert not outcome.ok
    assert outcome.result_type == "technical_failure"
    assert outcome.error_type == "model_rate_limited"
    assert outcome.attempts[-1]["will_retry"] is False
