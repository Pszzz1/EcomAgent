from __future__ import annotations

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionOutcome, ModelExecutionRunner

from .release_agent_contracts import (
    parse_initial_task_understanding,
    parse_turn_decision,
)
from .release_agent_prompts import (
    initial_task_messages,
    turn_decision_messages,
)


def available_turn_actions(task: ReleaseTask) -> list[str]:
    actions = ["explain"]
    if task.current_draft:
        actions.extend(["revise", "review"])
    else:
        actions.append("draft")
    if task.missing_context():
        actions.append("clarify")
    if len([item for item in task.revisions if item.status != "rejected"]) >= 2:
        actions.append("compare")
    if task.revisions:
        actions.append("restore")
    if task.pending_confirmation:
        actions.append("confirm")
    if task.current_draft:
        actions.append("generate_image")
    if task.promotion_image.get("asset_id"):
        actions.append("revise_image")
    return actions


class ReleaseAgentController:
    """Understands a release task and chooses the next semantic action."""

    def __init__(
        self,
        runner: ModelExecutionRunner,
    ) -> None:
        self.runner = runner

    def understand_initial_task(
        self,
        task: ReleaseTask,
        user_message: str,
    ) -> ModelExecutionOutcome:
        return self.runner.complete_structured(
            node_name="understand_initial_task",
            messages=initial_task_messages(task, user_message),
            response_format={"type": "json_object"},
            parser=lambda content: parse_initial_task_understanding(
                content,
                task,
                user_message,
            ),
        )

    def decide_turn(
        self,
        task: ReleaseTask,
        user_message: str,
        *,
        current_phase: str = "",
    ) -> ModelExecutionOutcome:
        available_actions = available_turn_actions(task)
        return self.runner.complete_structured(
            node_name="decide_turn",
            messages=turn_decision_messages(
                task,
                user_message,
                available_actions=available_actions,
                current_phase=current_phase,
            ),
            response_format={"type": "json_object"},
            parser=lambda content: parse_turn_decision(
                content,
                task,
                user_message,
                available_actions=available_actions,
            ),
        )
