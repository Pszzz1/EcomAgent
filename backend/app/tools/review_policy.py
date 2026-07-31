from __future__ import annotations

import re
from typing import Any, Dict, Iterable

from backend.app.domain import ReleaseTask

from .release_contracts import normalize_text


def build_review_report(
    task: ReleaseTask,
    *,
    content: str,
    revision: int,
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    decisions = [
        _canonical_decision(task, item, revision, index)
        for index, item in enumerate(parsed.get("decisions", []), start=1)
    ]
    missing_requirement_ids = [
        str(item) for item in parsed.get("missing_requirement_ids", [])
    ]
    blocking_requirement_ids = {
        str(item.get("requirement_id", ""))
        for item in decisions
        if item.get("action") in {"rewrite", "block", "confirm"}
    }
    blocking_requirement_ids.update(task.pending_compliance_requirement_ids())
    unfulfilled_ids = [
        requirement_id
        for requirement_id in missing_requirement_ids
        if requirement_id not in blocking_requirement_ids
    ]
    actions = {
        str(item.get("action", ""))
        for item in decisions
        if item.get("action") not in {"", "allow", "advisory"}
    }
    review_outcome = _review_outcome(
        actions,
        unfulfilled_ids,
        rewrite_mode=str(parsed.get("rewrite_mode", "none")),
        needs_more_context=bool(parsed.get("needs_more_context")),
    )
    summary = _review_summary(
        review_outcome,
        decisions=decisions,
        unfulfilled_ids=unfulfilled_ids,
        question=str(parsed.get("question", "")).strip(),
    )

    return {
        "revision": revision,
        "content": content,
        "publication_conclusion": _publication_conclusion(actions, unfulfilled_ids, review_outcome),
        "publication_action": _publication_action(actions, unfulfilled_ids, review_outcome),
        "review_outcome": review_outcome,
        "readiness_score": _readiness_score(actions, unfulfilled_ids, review_outcome),
        "summary": summary,
        "decisions": decisions,
        "unfulfilled_requirement_ids": unfulfilled_ids,
        "human_confirmation_items": [
            dict(item) for item in decisions if item.get("action") == "confirm"
        ],
    }


def resolve_review_confirmations(
    review: Dict[str, Any],
    *,
    resolutions: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    by_decision_id = {
        str(item.get("decision_id", "")): str(item.get("resolution", ""))
        for item in resolutions
    }
    resolved = dict(review)
    decisions = []
    for source in review.get("decisions", []):
        item = dict(source)
        if item.get("action") == "confirm":
            resolution = by_decision_id[str(item.get("decision_id", ""))]
            retain_claim = resolution == "confirmed_with_basis"
            item["action"] = "allow" if retain_claim else "rewrite"
            item["confirmation_resolution"] = resolution
            item["human_confirmation_eligible"] = False
            item["reason"] = (
                "用户已确认该宣传事实具有真实依据。"
                if retain_claim
                else "用户选择不保留待确认事实，转为风险改写。"
            )
        decisions.append(item)

    actions = {
        str(item.get("action", ""))
        for item in decisions
        if item.get("action") not in {"", "allow", "advisory"}
    }
    unfulfilled_ids = list(resolved.get("unfulfilled_requirement_ids", []))
    review_outcome = _review_outcome(
        actions,
        unfulfilled_ids,
        rewrite_mode="targeted" if "rewrite" in actions else "none",
        needs_more_context=False,
    )
    resolved.update(
        {
            "publication_conclusion": _publication_conclusion(
                actions, unfulfilled_ids, review_outcome
            ),
            "publication_action": _publication_action(
                actions, unfulfilled_ids, review_outcome
            ),
            "review_outcome": review_outcome,
            "readiness_score": _readiness_score(
                actions, unfulfilled_ids, review_outcome
            ),
            "decisions": decisions,
            "human_confirmation_items": [],
            "summary": _review_summary(
                review_outcome,
                decisions=decisions,
                unfulfilled_ids=unfulfilled_ids,
                question="",
            ),
        }
    )
    return resolved


def _canonical_decision(
    task: ReleaseTask,
    source: Dict[str, Any],
    revision: int,
    index: int,
) -> Dict[str, Any]:
    item = dict(source)
    requirement_id = str(item.get("requirement_id", ""))
    action = str(item.get("action", ""))
    reason = str(item.get("reason", ""))
    if action == "confirm":
        if _has_matching_confirmation(task, item):
            action = "allow"
        else:
            action, reason = _confirmation_disposition(task, item)
    severity = str(item.get("severity", "medium"))
    return {
        "decision_id": f"decision-{revision}-{index}",
        "origin": "requirement" if requirement_id else "draft_generated",
        "requirement_id": requirement_id,
        "matched_text": str(item.get("matched_text", "")),
        "label": str(item.get("label", "")),
        "risk_family": str(item.get("risk_family", "")),
        "severity": "high" if severity == "critical" else severity,
        "reason": reason,
        "human_confirmation_eligible": action == "confirm",
        "action": action,
    }


def _confirmation_disposition(
    task: ReleaseTask,
    decision: Dict[str, Any],
) -> tuple[str, str]:
    risk_family = str(decision.get("risk_family", ""))
    evidence_based_families = {
        "qualification",
        "endorsement_authorization",
        "unauthorized_endorsement",
        "regulated_effect",
        "guarantee_or_compensation",
    }
    if risk_family in evidence_based_families:
        return "confirm", str(decision.get("reason", ""))
    if risk_family != "conditional_promotion":
        return "rewrite", "该风险不依赖用户补充凭证，应直接改写而不是中断任务要求人工确认。"

    requirement_id = str(decision.get("requirement_id", ""))
    requirement = next(
        (item for item in task.active_requirements if item.requirement_id == requirement_id),
        None,
    )
    source_text = requirement.source_text if requirement is not None else ""
    if _contains_promotion_condition(source_text):
        return "confirm", str(decision.get("reason", ""))
    if _contains_promotion_condition(str(decision.get("matched_text", ""))):
        return "rewrite", "促销条件由工作稿新增，应自动删除或改写，不能转嫁给用户确认。"
    return "allow", "用户提供的是普通价格，当前表达不构成条件促销风险。"


def _contains_promotion_condition(value: str) -> bool:
    normalized = normalize_text(value)
    markers = (
        "限时",
        "限量",
        "库存",
        "活动价",
        "优惠券",
        "会员",
        "满减",
        "满赠",
        "秒杀",
        "清仓",
        "截止",
        "仅限",
    )
    return any(marker in normalized for marker in markers) or bool(
        re.search(r"满\d+.*减\d+|\d+(?:\.\d+)?折", normalized)
    )


def _has_matching_confirmation(task: ReleaseTask, decision: Dict[str, Any]) -> bool:
    requirement_id = str(decision.get("requirement_id", ""))
    requirement = next(
        (
            item
            for item in task.active_requirements
            if item.requirement_id == requirement_id
        ),
        None,
    )
    if requirement is None:
        return False
    return any(
        evidence.get("decision") == "confirmed_with_basis"
        and str(evidence.get("requirement_id", "")) == requirement_id
        and normalize_text(evidence.get("requirement_source_text", ""))
        == normalize_text(requirement.source_text)
        for evidence in task.confirmed_evidence
    )


def _review_outcome(
    actions: set[str],
    unfulfilled_ids: Iterable[str],
    *,
    rewrite_mode: str,
    needs_more_context: bool,
) -> str:
    if list(unfulfilled_ids):
        return "needs_requirement_revision"
    if actions.intersection({"rewrite", "block"}):
        return (
            "needs_full_redraft"
            if rewrite_mode == "full"
            else "needs_targeted_rewrite"
        )
    if "confirm" in actions:
        return "needs_confirmation"
    if needs_more_context:
        return "needs_more_context"
    return "safe"


def _publication_conclusion(
    actions: set[str],
    unfulfilled_ids: Iterable[str],
    review_outcome: str,
) -> str:
    if "block" in actions:
        return "prohibit_publish"
    if actions or list(unfulfilled_ids) or review_outcome == "needs_more_context":
        return "revise_before_publish"
    return "safe_to_publish"


def _publication_action(
    actions: set[str],
    unfulfilled_ids: Iterable[str],
    review_outcome: str,
) -> str:
    if "block" in actions:
        return "block_directly"
    if actions.intersection({"rewrite"}) or list(unfulfilled_ids):
        return "revise_required"
    if "confirm" in actions or review_outcome == "needs_more_context":
        return "human_review_required"
    return "allow"


def _readiness_score(
    actions: set[str],
    unfulfilled_ids: Iterable[str],
    review_outcome: str,
) -> int:
    if "block" in actions:
        return 0
    if list(unfulfilled_ids):
        return 30
    if "rewrite" in actions:
        return 40
    if "confirm" in actions or review_outcome == "needs_more_context":
        return 70
    return 100


def _review_summary(
    review_outcome: str,
    *,
    decisions: list[Dict[str, Any]],
    unfulfilled_ids: list[str],
    question: str,
) -> str:
    if review_outcome == "needs_requirement_revision":
        return f"当前版本有 {len(unfulfilled_ids)} 项用户要求未落实，需补全后复审。"
    if review_outcome == "needs_full_redraft":
        return "当前版本存在贯穿全文的风险表达，需要重新起草并复审。"
    if review_outcome == "needs_targeted_rewrite":
        risk_count = sum(
            item.get("action") in {"rewrite", "block"} for item in decisions
        )
        return f"当前版本有 {risk_count} 处风险表达需要修改并复审。"
    if review_outcome == "needs_confirmation":
        confirmation_count = sum(
            item.get("action") == "confirm" for item in decisions
        )
        return f"当前版本有 {confirmation_count} 项宣传事实需要确认依据。"
    if review_outcome == "needs_more_context":
        return question or "请补充完成审核所需的信息。"
    return "审核通过，当前版本可发布。"
