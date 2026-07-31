from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionOutcome, ModelExecutionRunner
from backend.app.knowledge.ad_law_retriever import AdLawRetriever
from backend.app.knowledge.platform_retriever import PlatformPolicyRetriever

from .release_contracts import (
    parse_review,
    validate_numeric_claims,
)
from .release_prompts import draft_messages, review_messages, rewrite_messages
from .review_policy import build_review_report


@dataclass
class ToolOutcome:
    ok: bool
    tool_name: str
    parsed: Dict[str, Any] = field(default_factory=dict)
    review: Dict[str, Any] = field(default_factory=dict)
    attempts: list[Dict[str, Any]] = field(default_factory=list)
    tool_calls: list[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_type: str = ""
    result_type: str = "success"

    @classmethod
    def from_model(cls, tool_name: str, outcome: ModelExecutionOutcome) -> "ToolOutcome":
        return cls(
            ok=outcome.ok,
            tool_name=tool_name,
            parsed=dict(outcome.parsed),
            attempts=list(outcome.attempts),
            error=outcome.error,
            error_type=outcome.error_type,
            result_type=outcome.result_type,
        )


class DraftCopyTool:
    name = "draft_copy"

    def __init__(self, runner: ModelExecutionRunner) -> None:
        self.runner = runner

    def run(
        self,
        task: ReleaseTask,
        *,
        mode: str,
        instruction: str,
        excluded_requirements: list[str] | None = None,
    ) -> ToolOutcome:
        outcome = self.runner.complete_structured(
            node_name=self.name,
            messages=draft_messages(
                task,
                mode=mode,
                instruction=instruction,
                excluded_requirements=excluded_requirements or [],
            ),
            response_format={"type": "json_object"},
            parser=lambda value: validate_numeric_claims(
                value,
                task,
                preserve_current_draft=mode == "revision",
            ),
        )
        return ToolOutcome.from_model(self.name, outcome)


class CandidateReviewTool:
    name = "candidate_review"

    def __init__(self, runner: ModelExecutionRunner) -> None:
        self.runner = runner
        self.ad_law = AdLawRetriever()
        self.platform_policy = PlatformPolicyRetriever()

    def run(self, task: ReleaseTask, *, content: str, revision: int) -> ToolOutcome:
        tool_calls: list[Dict[str, Any]] = []
        review_content = content
        started = time.perf_counter()
        try:
            law = self.ad_law.retrieve(content=review_content, category=task.product_category or "general")
            tool_calls.append(_retrieval_call("ad_law_knowledge", started, "ok"))
        except Exception as exc:
            return ToolOutcome(
                ok=False,
                tool_name=self.name,
                tool_calls=[_retrieval_call("ad_law_knowledge", started, "failed", str(exc))],
                error=str(exc),
                error_type="knowledge_retrieval_error",
                result_type="technical_failure",
            )

        started = time.perf_counter()
        try:
            platform = self.platform_policy.retrieve(
                content=review_content,
                platform=task.platform or "general",
                category=task.product_category or "general",
            )
            tool_calls.append(_retrieval_call("platform_policy", started, "ok"))
        except Exception as exc:
            return ToolOutcome(
                ok=False,
                tool_name=self.name,
                tool_calls=[*tool_calls, _retrieval_call("platform_policy", started, "failed", str(exc))],
                error=str(exc),
                error_type="knowledge_retrieval_error",
                result_type="technical_failure",
            )

        compliance_targets = task.pending_compliance_requirement_ids()
        expected_requirements = {
            item.requirement_id: item.source_text
            for item in task.active_requirements
            if item.requirement_id not in compliance_targets
        }
        outcome = self.runner.complete_structured(
            node_name=self.name,
            messages=review_messages(
                task,
                content,
                law.to_dict(),
                platform.to_dict(),
                excluded_requirement_ids=compliance_targets,
            ),
            response_format={"type": "json_object"},
            parser=lambda value: parse_review(
                value,
                expected_requirements,
                content,
            ),
        )
        if not outcome.ok:
            return ToolOutcome(
                ok=False,
                tool_name=self.name,
                attempts=outcome.attempts,
                tool_calls=tool_calls,
                error=outcome.error,
                error_type=outcome.error_type,
                result_type=outcome.result_type,
            )
        review = build_review_report(
            task,
            content=content,
            revision=revision,
            parsed=outcome.parsed,
        )
        return ToolOutcome(
            ok=True,
            tool_name=self.name,
            review=review,
            attempts=outcome.attempts,
            tool_calls=tool_calls,
        )


class RiskRewriteTool:
    name = "risk_rewrite"

    def __init__(self, runner: ModelExecutionRunner) -> None:
        self.runner = runner

    def run(self, task: ReleaseTask, *, review: Dict[str, Any]) -> ToolOutcome:
        outcome = self.runner.complete_structured(
            node_name=self.name,
            messages=rewrite_messages(task, review),
            response_format={"type": "json_object"},
            parser=lambda value: validate_numeric_claims(
                value,
                task,
                preserve_current_draft=True,
            ),
        )
        return ToolOutcome.from_model(self.name, outcome)


def _retrieval_call(name: str, started: float, status: str, error: str = "") -> Dict[str, Any]:
    return {
        "tool_name": name,
        "status": status,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "error": error,
        "error_type": "knowledge_retrieval_error" if error else "",
    }
