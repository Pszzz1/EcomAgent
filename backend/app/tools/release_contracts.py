from __future__ import annotations

import re
from typing import Any, Dict, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.infrastructure.modeling.structured_output.common import _loads


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_draft: str = Field(min_length=1)


ReviewRiskFamily = Literal[
    "prohibited_superlative",
    "absolute_superlative",
    "ranking",
    "competitor_comparison",
    "misleading_promotion",
    "medicalized_claim",
    "false_commitment",
    "education_commitment",
    "finance_return_commitment",
    "real_estate_mislead",
    "qualification",
    "endorsement_authorization",
    "unauthorized_endorsement",
    "regulated_effect",
    "guarantee_or_compensation",
    "conditional_promotion",
    "platform_hard",
    "platform_revise",
    "external_contact_diversion",
    "private_transaction",
    "engagement_bait",
    "third_party_platform_diversion",
    "commercial_disclosure_risk",
    "non_publishable_meta_content",
    "unprovided_material_claim",
]
REVIEW_RISK_FAMILIES = tuple(get_args(ReviewRiskFamily))


class ReviewDecisionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirement_id: str = ""
    matched_text: str = Field(min_length=1)
    label: str = Field(min_length=1)
    risk_family: ReviewRiskFamily
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    action: Literal["advisory", "rewrite", "block", "confirm"]
    reason: str = Field(min_length=1)

    @field_validator("requirement_id", mode="before")
    @classmethod
    def normalize_empty_requirement_id(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def confirmation_requires_requirement(self) -> "ReviewDecisionOutput":
        if self.action == "confirm" and not self.requirement_id:
            raise ValueError("confirmation_requires_requirement_id")
        return self


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rewrite_mode: Literal["none", "targeted", "full"] = "none"
    needs_more_context: bool = False
    question: str = ""
    decisions: list[ReviewDecisionOutput] = Field(default_factory=list)
    missing_requirement_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_route(self) -> "ReviewOutput":
        requires_rewrite = any(
            item.action in {"rewrite", "block"} for item in self.decisions
        )
        if requires_rewrite and self.rewrite_mode == "none":
            raise ValueError("rewrite_decision_requires_rewrite_mode")
        if not requires_rewrite and self.rewrite_mode != "none":
            self.rewrite_mode = "none"
        if self.needs_more_context and not self.question.strip():
            raise ValueError("missing_context_requires_question")
        return self


def parse_draft(content: str) -> Dict[str, Any]:
    return DraftOutput.model_validate(_loads(content)).model_dump()


def validate_numeric_claims(
    draft: str,
    task: Any,
    *,
    preserve_current_draft: bool = False,
) -> Dict[str, Any]:
    parsed = parse_draft(draft)
    allowed_source = " ".join(
        [
            str(getattr(task, "task_brief", "")),
            str(getattr(task, "product_name", "")),
            str(getattr(task, "product_category", "")),
            str(getattr(task, "objective", "")),
            *[
                str(item.source_text or item.text)
                for item in getattr(task, "requirements", [])
            ],
            *[
                str(item)
                for item in getattr(task, "image_analysis", {}).get(
                    "visible_text", []
                )
            ],
            str(getattr(task, "current_draft", ""))
            if preserve_current_draft
            else "",
        ]
    )
    allowed = set(_numeric_tokens(allowed_source))
    added = sorted(set(_numeric_tokens(parsed["primary_draft"])) - allowed)
    if added:
        raise ValueError("unprovided_numeric_claims:" + ",".join(added))
    return parsed


def parse_review(
    content: str,
    expected_requirements: Dict[str, str],
    reviewed_content: str,
) -> Dict[str, Any]:
    parsed = ReviewOutput.model_validate(_loads(content)).model_dump()
    expected = {str(key): str(value) for key, value in expected_requirements.items()}
    parsed["missing_requirement_ids"] = list(
        dict.fromkeys(
            str(item)
            for item in parsed["missing_requirement_ids"]
            if str(item) in expected
        )
    )
    normalized_decisions = []
    for decision in parsed["decisions"]:
        requirement_id = str(decision.get("requirement_id", ""))
        matched_text = str(decision.get("matched_text", "")).strip()
        if requirement_id not in expected:
            matching_ids = [
                candidate_id
                for candidate_id, requirement_text in expected.items()
                if _texts_overlap(matched_text, requirement_text)
            ]
            if len(matching_ids) == 1:
                requirement_id = matching_ids[0]
                decision["requirement_id"] = requirement_id
            elif decision.get("action") == "confirm":
                raise ValueError("confirmation_requires_known_requirement")
            else:
                decision["requirement_id"] = ""
        requirement_text = expected.get(str(decision.get("requirement_id", "")), "")
        quoted_from_content = matched_text in reviewed_content
        quoted_omitted_requirement = bool(
            requirement_text
            and normalize_text(matched_text) == normalize_text(requirement_text)
        )
        if not quoted_from_content and not quoted_omitted_requirement:
            raise ValueError("review_decision_quote_not_grounded")
        if (
            decision.get("risk_family") == "unprovided_material_claim"
            and decision.get("requirement_id")
        ):
            if normalize_text(matched_text) == normalize_text(requirement_text):
                continue
            decision["requirement_id"] = ""
        normalized_decisions.append(decision)
    parsed["decisions"] = normalized_decisions
    if not any(
        item.get("action") in {"rewrite", "block"}
        for item in normalized_decisions
    ):
        parsed["rewrite_mode"] = "none"
    return parsed


def _texts_overlap(left: str, right: str) -> bool:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    return bool(
        normalized_left
        and normalized_right
        and (
            normalized_left in normalized_right
            or normalized_right in normalized_left
        )
    )


def _numeric_tokens(value: str) -> list[str]:
    return re.findall(
        r"(?<![A-Za-z0-9])(?:\d+(?:\.\d+)?%?|[A-Za-z]+\d+)(?![A-Za-z0-9])",
        value,
    )


def _normalize_requirement_phrase(value: object) -> str:
    text = re.sub(r"[¥￥]\s*(\d)", r"\1元", str(value))
    units = "包件瓶盒支个套"
    text = re.sub(rf"[/／]([{units}])", r"每\1", text)
    text = re.sub(rf"一([{units}])", r"每\1", text)
    return normalize_text(text)


def normalize_text(value: object) -> str:
    return "".join(
        char.lower()
        for char in str(value)
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )
