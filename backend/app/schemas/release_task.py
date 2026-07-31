from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleaseTaskInput(StrictModel):
    task_id: Optional[str] = None
    task_brief: str = ""
    product_name: str = ""
    product_category: str = ""
    platform: str = ""
    objective: str = ""
    selling_points: List[str] = Field(default_factory=list)
    draft_copy: str = ""
    brand_constraints: List[str] = Field(default_factory=list)
    source_image_asset_id: str = ""


class ConfirmationResolutionInput(StrictModel):
    decision_id: str = Field(min_length=1)
    resolution: Literal["confirmed_with_basis", "rewrite_without_basis"]
    evidence_notes: str = ""


class ReleaseTaskTurnInput(StrictModel):
    message: str = ""
    confirmation_resolutions: List[ConfirmationResolutionInput] = Field(default_factory=list)
    turn_id: str = ""
    expected_state_version: Optional[int] = Field(default=None, ge=0)


class RequirementState(StrictModel):
    requirement_id: str
    text: str
    source_text: str
    kind: str
    status: str
    source: str
    created_turn: str


class ReviewDecision(StrictModel):
    decision_id: str
    origin: Literal["requirement", "draft_generated"]
    requirement_id: str = ""
    matched_text: str
    label: str
    risk_family: str
    severity: Literal["low", "medium", "high"]
    reason: str
    disposition_reason: str = ""
    confirmation_resolution: Literal[
        "", "confirmed_with_basis", "rewrite_without_basis"
    ] = ""
    human_confirmation_eligible: bool
    action: Literal["allow", "advisory", "block", "rewrite", "confirm"]


class ReviewReport(StrictModel):
    revision: int
    content: str
    publication_conclusion: Literal[
        "safe_to_publish", "revise_before_publish", "prohibit_publish"
    ]
    publication_action: Literal[
        "allow", "revise_required", "block_directly", "human_review_required"
    ]
    review_outcome: Literal[
        "safe",
        "needs_targeted_rewrite",
        "needs_full_redraft",
        "needs_requirement_revision",
        "needs_confirmation",
        "needs_more_context",
    ]
    readiness_score: int = Field(ge=0, le=100)
    summary: str
    decisions: List[ReviewDecision]
    unfulfilled_requirement_ids: List[str]
    human_confirmation_items: List[ReviewDecision]


class RevisionState(StrictModel):
    revision: int
    content: str
    source: str
    instruction: str
    status: str
    review: Optional[ReviewReport] = None

    @field_validator("review", mode="before")
    @classmethod
    def empty_review_is_none(cls, value: Any) -> Any:
        return None if value == {} else value


class ReviewComparisonItem(StrictModel):
    revision: int
    publication_conclusion: str
    readiness_score: int


class ReviewComparison(StrictModel):
    previous: ReviewComparisonItem
    current: ReviewComparisonItem


class PendingConfirmation(StrictModel):
    revision: int
    items: List[ReviewDecision]
    review: ReviewReport


class ConfirmedEvidence(StrictModel):
    requirement_id: str
    requirement_source_text: str = ""
    matched_text: str
    risk_family: str
    decision: str
    comment: str


class ConversationEntry(StrictModel):
    role: Literal["user", "assistant"]
    content: str
    phase: str = ""
    status: str = ""
    decision: str = ""


class TaskEvent(StrictModel):
    event: str
    revision: int = 0
    source: str = ""
    conclusion: str = ""
    error_type: str = ""
    reason: str = ""


class PlatformContent(StrictModel):
    platform: str
    title: str
    body: str
    script: str


class RequirementDelivery(StrictModel):
    requirement_id: str
    requirement: str
    status: str


class ReleasePackage(StrictModel):
    package_status: Literal["ready_to_publish"]
    platform: str
    product_name: str
    product_category: str
    revision: int
    risk_status: Literal["safe_to_publish"]
    readiness_score: int = Field(ge=0, le=100)
    review_summary: str
    final_copy: str
    promotion_image_asset_id: str
    promotion_image_text: List[str]
    platform_content: PlatformContent
    requirement_delivery: List[RequirementDelivery]
    review_decisions: List[ReviewDecision]
    confirmed_evidence: List[ConfirmedEvidence]
    pending_items: List[str]
    publish_checklist: List[str]


class LastTurnError(StrictModel):
    error_type: str = ""
    reason: str


class PromotionImageState(StrictModel):
    asset_id: str
    display_text: List[str]
    prompt: str
    instruction: str
    copy_revision: int
    status: Literal["awaiting_user", "accepted", "stale"]


class ReleaseTaskState(StrictModel):
    schema_version: int
    task_id: str
    task_brief: str
    product_name: str
    product_category: str
    platform: str
    objective: str
    source_image_asset_id: str
    image_analysis: Dict[str, Any]
    promotion_image: Optional[PromotionImageState] = None
    requirements: List[RequirementState]
    active_requirements: List[RequirementState]
    revisions: List[RevisionState]
    current_revision: int
    current_draft: str
    draft_origin: str
    current_review: Optional[ReviewReport] = None
    review_comparison: Optional[ReviewComparison] = None
    pending_confirmation: Optional[PendingConfirmation] = None
    confirmed_evidence: List[ConfirmedEvidence]
    conversation: List[ConversationEntry]
    events: List[TaskEvent]
    final_release_package: Optional[ReleasePackage] = None
    last_turn_error: Optional[LastTurnError] = None
    state_version: int
    next_requirement_number: int

    @field_validator(
        "current_review",
        "review_comparison",
        "pending_confirmation",
        "promotion_image",
        "final_release_package",
        "last_turn_error",
        mode="before",
    )
    @classmethod
    def empty_object_is_none(cls, value: Any) -> Any:
        return None if value == {} else value


class TraceEvent(StrictModel):
    stage: str
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)


class ReleaseTaskResult(StrictModel):
    task_id: str
    status: str
    phase: str
    answer: str = ""
    next_questions: List[str] = Field(default_factory=list)
    state: ReleaseTaskState
    trace_events: List[TraceEvent] = Field(default_factory=list)


class ReleaseTaskSummary(StrictModel):
    task_id: str
    status: str
    phase: str
    product_name: str = ""
    product_category: str = ""
    platform: str = ""
    current_revision: int = 0
    updated_at: str


class APIErrorDetail(StrictModel):
    code: str
    message: str


class APIErrorResponse(StrictModel):
    detail: APIErrorDetail
