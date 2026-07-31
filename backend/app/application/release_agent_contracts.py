from __future__ import annotations

from typing import Any, Collection, Dict, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.modeling.structured_output.common import _loads


class NewRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_text: str = ""
    kind: Literal["fact", "selling_point", "style", "content"] = "content"
    replaces_requirement_id: str = ""


class TaskFieldUpdates(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_name: str = ""
    product_category: str = ""
    platform: Literal["", "douyin", "kuaishou", "xiaohongshu"] = ""
    objective: str = ""


class TurnConfirmationResolution(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision_id: str = Field(min_length=1)
    resolution: Literal["confirmed_with_basis", "rewrite_without_basis"]


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_text: str = Field(min_length=1)
    kind: Literal["fact", "selling_point", "style", "content"] = "content"


class InitialTaskUnderstanding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1)
    task_updates: TaskFieldUpdates = Field(default_factory=TaskFieldUpdates)
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    instruction: str = ""


class TurnDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Literal[
        "draft",
        "revise",
        "review",
        "explain",
        "compare",
        "restore",
        "confirm",
        "generate_image",
        "revise_image",
        "clarify",
    ]
    summary: str = Field(min_length=1)
    task_updates: TaskFieldUpdates = Field(default_factory=TaskFieldUpdates)
    remove_requirement_ids: list[str] = Field(default_factory=list)
    new_requirements: list[NewRequirement] = Field(default_factory=list)
    reactivate_requirement_ids: list[str] = Field(default_factory=list)
    confirmation_resolutions: list[TurnConfirmationResolution] = Field(default_factory=list)
    answer: str = ""
    question: str = ""
    target_revision: int = Field(default=0, ge=0)
    revision_target: Literal["", "copy", "image"] = ""

    @model_validator(mode="after")
    def validate_intent_payload(self) -> "TurnDecision":
        if self.intent == "clarify" and not self.question.strip():
            raise ValueError("clarify_requires_question")
        if self.intent == "explain" and not self.answer.strip():
            raise ValueError("explain_requires_answer")
        if self.intent == "confirm" and not self.confirmation_resolutions:
            raise ValueError("confirm_requires_pending_resolutions")
        if self.intent != "restore":
            self.target_revision = 0
        if any(not requirement_id.strip() for requirement_id in self.remove_requirement_ids):
            raise ValueError("remove_requirement_id_cannot_be_empty")
        if any(not requirement_id.strip() for requirement_id in self.reactivate_requirement_ids):
            raise ValueError("reactivate_requirement_id_cannot_be_empty")
        for item in self.new_requirements:
            if not item.source_text.strip():
                raise ValueError("new_requirement_requires_source_text")
        return self


def parse_turn_decision(
    content: str,
    task: ReleaseTask,
    user_message: str,
    *,
    available_actions: Collection[str],
    current_phase: str = "",
) -> Dict[str, Any]:
    decision = TurnDecision.model_validate(_loads(content))
    if decision.intent not in available_actions:
        raise ValueError(f"action_not_available:{decision.intent}")
    if decision.intent == "revise" and decision.revision_target == "image":
        raise ValueError("copy_revision_cannot_target_image")
    if decision.intent == "revise_image" and decision.revision_target == "copy":
        raise ValueError("image_revision_cannot_target_copy")
    if (
        current_phase
        in {"promotion_image_review_ready", "promotion_image_revision_needed"}
        and decision.intent == "revise"
        and decision.revision_target != "copy"
    ):
        raise ValueError(
            "image_phase_feedback_requires_revise_image_or_explicit_copy_target"
        )
    existing_ids = {item.requirement_id for item in task.active_requirements}
    reactivatable_ids = {
        item.requirement_id
        for item in task.requirements
        if item.status == "removed_for_compliance"
    }
    replacement_ids = [
        item.replaces_requirement_id
        for item in decision.new_requirements
        if item.replaces_requirement_id
    ]
    removed_ids = [*decision.remove_requirement_ids, *replacement_ids]
    if decision.intent in {"draft", "revise"}:
        if len(removed_ids) != len(set(removed_ids)):
            raise ValueError("duplicate_removed_requirement_id")
        unknown = sorted(set(removed_ids) - existing_ids)
        if unknown:
            raise ValueError(f"unknown_removed_requirement_id:{unknown}")
    else:
        decision.task_updates = TaskFieldUpdates()
        decision.remove_requirement_ids = []
        decision.new_requirements = []
        removed_ids = []
    if decision.intent not in {"restore", "revise"}:
        decision.reactivate_requirement_ids = []
    unknown_reactivations = sorted(
        set(decision.reactivate_requirement_ids) - reactivatable_ids
    )
    if unknown_reactivations:
        raise ValueError(f"unknown_reactivatable_requirement_id:{unknown_reactivations}")
    if decision.intent == "confirm":
        if not task.pending_confirmation:
            raise ValueError("confirm_requires_pending_confirmation")
        expected_confirmation_ids = {
            str(item.get("decision_id", ""))
            for item in task.pending_confirmation.get("items", [])
        }
        returned_confirmation_ids = {
            item.decision_id for item in decision.confirmation_resolutions
        }
        if decision.confirmation_resolutions and (
            returned_confirmation_ids != expected_confirmation_ids
            or len(returned_confirmation_ids) != len(decision.confirmation_resolutions)
        ):
            raise ValueError("confirmation_resolutions_must_cover_pending_items_once")
    else:
        decision.confirmation_resolutions = []
    ungrounded = []
    for item in decision.new_requirements:
        grounded_text = _source_span(item.source_text, user_message)
        if grounded_text is None:
            ungrounded.append("".join(item.source_text.split()))
        else:
            item.source_text = grounded_text
    if ungrounded:
        raise ValueError("requirement_mutation_not_grounded:" + "|".join(ungrounded))
    parsed = decision.model_dump()
    parsed["instruction"] = (
        user_message.strip()
        if decision.intent in {"draft", "revise"} or decision.reactivate_requirement_ids
        else ""
    )
    parsed["requirement_mutations"] = [
        {"operation": "remove", "requirement_id": requirement_id}
        for requirement_id in removed_ids
    ] + [
        {
            "operation": "add",
            "source_text": item.source_text,
            "kind": item.kind,
        }
        for item in decision.new_requirements
    ]
    return parsed


def parse_initial_task_understanding(
    content: str,
    task: ReleaseTask,
    user_message: str,
) -> Dict[str, Any]:
    parsed = InitialTaskUnderstanding.model_validate(_loads(content))
    _fill_observed_initial_context(parsed.task_updates, task, user_message)
    grounded_requirements = [
        (item, _source_span(item.source_text, user_message)) for item in parsed.requirements
    ]
    source_texts = [text for _, text in grounded_requirements if text is not None]
    ungrounded = [
        "".join(item.source_text.split())
        for item, grounded_text in grounded_requirements
        if grounded_text is None
    ]
    if ungrounded:
        raise ValueError("initial_requirement_not_grounded:" + "|".join(ungrounded))
    if len(source_texts) != len(set(source_texts)):
        raise ValueError("duplicate_initial_requirement")
    _validate_initial_task_updates(parsed.task_updates, task, user_message)
    intent = "review" if task.current_draft else "draft"
    return {
        "intent": intent,
        "summary": parsed.summary,
        "task_updates": parsed.task_updates.model_dump(),
        "requirement_mutations": [
            {
                "operation": "add",
                "source_text": grounded_text or "",
                "kind": item.kind,
            }
            for item, grounded_text in grounded_requirements
        ],
        "instruction": parsed.instruction.strip() or user_message.strip(),
        "answer": "",
        "question": "",
        "reactivate_requirement_ids": [],
        "confirmation_resolutions": [],
    }
def _fill_observed_initial_context(
    updates: TaskFieldUpdates,
    task: ReleaseTask,
    user_message: str,
) -> None:
    if not updates.platform and not task.platform:
        updates.platform = _platform_from_user_message(user_message)
    if not (
        updates.product_name
        or updates.product_category
        or task.product_name
        or task.product_category
    ):
        updates.product_category = str(task.image_analysis.get("product_type", "")).strip()


def _validate_initial_task_updates(
    updates: TaskFieldUpdates,
    task: ReleaseTask,
    user_message: str,
) -> None:
    current_platform = task.platform.strip().lower()
    returned_platform = str(updates.platform).strip().lower()
    if returned_platform and returned_platform != current_platform:
        if _platform_from_user_message(user_message) != returned_platform:
            raise ValueError(f"initial_context_not_grounded:platform:{returned_platform}")

    mentioned_platform = _platform_from_user_message(user_message)
    effective_platform = returned_platform or current_platform
    if mentioned_platform and effective_platform != mentioned_platform:
        raise ValueError(f"initial_context_missing_platform:{mentioned_platform}")


def _platform_from_user_message(user_message: str) -> str:
    aliases = {
        "抖音": "douyin",
        "douyin": "douyin",
        "快手": "kuaishou",
        "kuaishou": "kuaishou",
        "小红书": "xiaohongshu",
        "xiaohongshu": "xiaohongshu",
    }
    lowered = user_message.lower()
    for alias, platform in aliases.items():
        if alias in lowered:
            return platform
    return ""


def _source_span(source_text: str, user_message: str) -> str | None:
    candidate = source_text.strip()
    if candidate and candidate in user_message:
        return candidate
    compact_candidate = "".join(candidate.split())
    indexed_chars = [
        (index, char) for index, char in enumerate(user_message) if not char.isspace()
    ]
    compact_message = "".join(char for _, char in indexed_chars)
    start = compact_message.find(compact_candidate)
    if not compact_candidate or start < 0:
        return None
    end = start + len(compact_candidate) - 1
    return user_message[indexed_chars[start][0] : indexed_chars[end][0] + 1].strip()
