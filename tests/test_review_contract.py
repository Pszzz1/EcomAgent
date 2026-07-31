import json

import pytest

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionRunner, RetryPolicy
from backend.app.schemas.release_task import ReviewReport
from backend.app.tools.release_contracts import (
    parse_draft,
    parse_review,
    validate_numeric_claims,
)
from backend.app.tools.release_prompts import (
    compact_knowledge,
    draft_messages,
    review_messages,
    rewrite_messages,
)
from backend.app.tools.release_tools import DraftCopyTool, RiskRewriteTool
from backend.app.tools.review_policy import (
    build_review_report,
    resolve_review_confirmations,
)
from tests.fakes import QueueModelProvider


def _task(requirement: str = "500ml") -> ReleaseTask:
    task = ReleaseTask(task_id="task-1", platform="xiaohongshu", product_name="测试商品")
    task.add_requirement(requirement, kind="fact")
    return task


def _parsed(*, decisions=None, status="fulfilled", **updates) -> dict:
    value = {
        "rewrite_mode": "none",
        "needs_more_context": False,
        "question": "",
        "decisions": list(decisions or []),
        "missing_requirement_ids": ["req-1"] if status == "missing" else [],
    }
    value.update(updates)
    return value


def _decision(action: str, *, requirement_id="req-1", matched_text="500ml") -> dict:
    return {
        "requirement_id": requirement_id,
        "matched_text": matched_text,
        "label": "测试风险",
        "risk_family": "misleading_promotion",
        "severity": "high",
        "action": action,
        "reason": "当前表达需要相应处置",
    }


def _model_payload(value: dict) -> dict:
    return json.loads(json.dumps(value, ensure_ascii=False))


def test_safe_review_has_no_unfulfilled_requirements() -> None:
    task = _task()
    review = build_review_report(
        task,
        content="测试商品容量500ml。",
        revision=1,
        parsed=_parsed(),
    )

    assert review["publication_conclusion"] == "safe_to_publish"
    assert review["review_outcome"] == "safe"
    assert review["unfulfilled_requirement_ids"] == []


def test_missing_normal_requirement_routes_to_one_draft_correction() -> None:
    task = _task()
    review = build_review_report(
        task,
        content="测试商品。",
        revision=1,
        parsed=_parsed(status="missing"),
    )

    assert review["review_outcome"] == "needs_requirement_revision"
    assert review["unfulfilled_requirement_ids"] == ["req-1"]


@pytest.mark.parametrize(
    ("action", "expected_outcome", "expected_conclusion"),
    [
        ("rewrite", "needs_targeted_rewrite", "revise_before_publish"),
        ("block", "needs_targeted_rewrite", "prohibit_publish"),
        ("confirm", "needs_confirmation", "revise_before_publish"),
    ],
)
def test_valid_review_action_is_preserved_by_canonical_policy(
    action: str,
    expected_outcome: str,
    expected_conclusion: str,
) -> None:
    task = _task()
    decision = _decision(action)
    if action == "confirm":
        decision["risk_family"] = "qualification"
    parsed = _parsed(decisions=[decision])
    if action in {"rewrite", "block"}:
        parsed["rewrite_mode"] = "targeted"

    review = build_review_report(
        task,
        content="测试商品容量500ml。",
        revision=1,
        parsed=parsed,
    )

    assert review["decisions"][0]["action"] == action
    assert review["review_outcome"] == expected_outcome
    assert review["publication_conclusion"] == expected_conclusion


def test_advisory_is_publishable_and_does_not_trigger_rewrite() -> None:
    task = _task()
    review = build_review_report(
        task,
        content="测试商品容量500ml，日常使用很轻松。",
        revision=1,
        parsed=_parsed(decisions=[_decision("advisory", matched_text="很轻松")]),
    )

    assert review["review_outcome"] == "safe"
    assert review["publication_conclusion"] == "safe_to_publish"
    assert review["decisions"][0]["action"] == "advisory"
    assert ReviewReport.model_validate(review).decisions[0].action == "advisory"


def test_risky_requirement_missing_from_copy_is_not_misrouted_as_draft_omission() -> None:
    task = _task("国产品牌第一")
    parsed = _parsed(
        decisions=[_decision("rewrite", matched_text="国产品牌第一")],
        status="missing",
        rewrite_mode="targeted",
    )
    review = build_review_report(
        task,
        content="国产品牌第一",
        revision=1,
        parsed=parsed,
    )

    assert review["unfulfilled_requirement_ids"] == []
    assert review["review_outcome"] == "needs_targeted_rewrite"


def test_full_rewrite_mode_routes_to_full_redraft() -> None:
    task = _task()
    review = build_review_report(
        task,
        content="测试商品容量500ml。",
        revision=1,
        parsed=_parsed(decisions=[_decision("rewrite")], rewrite_mode="full"),
    )

    assert review["review_outcome"] == "needs_full_redraft"


def test_more_context_uses_review_question_without_creating_risk() -> None:
    task = _task()
    review = build_review_report(
        task,
        content="测试商品容量500ml。",
        revision=1,
        parsed=_parsed(
            needs_more_context=True,
            question="请补充必要信息。",
            summary="",
        ),
    )

    assert review["review_outcome"] == "needs_more_context"
    assert review["summary"] == "请补充必要信息。"


def test_matching_human_confirmation_converts_only_repeated_confirm_to_allow() -> None:
    task = _task("权威认证")
    task.confirmed_evidence = [
        {
            "requirement_id": "req-1",
            "requirement_source_text": "权威认证",
            "matched_text": "权威认证",
            "risk_family": "qualification",
            "decision": "confirmed_with_basis",
        }
    ]
    decision = _decision("confirm", matched_text="已通过权威认证")
    decision["risk_family"] = "unauthorized_endorsement"
    review = build_review_report(
        task,
        content="测试商品已通过权威认证。",
        revision=2,
        parsed=_parsed(decisions=[decision]),
    )

    assert review["decisions"][0]["action"] == "allow"
    assert review["review_outcome"] == "safe"


def test_confirmation_rewrite_preserves_resolution_context_for_rewrite() -> None:
    decision = _decision("confirm", matched_text="活动价19.9元")
    decision["risk_family"] = "conditional_promotion"
    review = build_review_report(
        _task("活动价19.9元"),
        content="活动价19.9元",
        revision=1,
        parsed=_parsed(decisions=[decision]),
    )

    resolved = resolve_review_confirmations(
        review,
        resolutions=[
            {
                "decision_id": review["decisions"][0]["decision_id"],
                "resolution": "rewrite_without_basis",
            }
        ],
    )

    assert resolved["decisions"][0]["action"] == "rewrite"
    assert (
        resolved["decisions"][0]["confirmation_resolution"]
        == "rewrite_without_basis"
    )


def test_confirmation_resolutions_apply_independently_per_review_item() -> None:
    task = _task("宣传事实A")
    task.add_requirement("宣传事实B", kind="fact")
    first = _decision("confirm", requirement_id="req-1", matched_text="宣传事实A")
    first["risk_family"] = "qualification"
    second = _decision("confirm", requirement_id="req-2", matched_text="宣传事实B")
    second["risk_family"] = "qualification"
    review = build_review_report(
        task,
        content="测试商品包含宣传事实A和宣传事实B。",
        revision=1,
        parsed=_parsed(decisions=[first, second]),
    )

    resolved = resolve_review_confirmations(
        review,
        resolutions=[
            {
                "decision_id": review["decisions"][0]["decision_id"],
                "resolution": "confirmed_with_basis",
            },
            {
                "decision_id": review["decisions"][1]["decision_id"],
                "resolution": "rewrite_without_basis",
            },
        ],
    )

    assert [item["action"] for item in resolved["decisions"]] == ["allow", "rewrite"]
    assert resolved["review_outcome"] == "needs_targeted_rewrite"
    assert resolved["human_confirmation_items"] == []


def test_ordinary_price_cannot_be_escalated_to_human_confirmation() -> None:
    decision = _decision("confirm", matched_text="只要2元一包")
    decision["risk_family"] = "conditional_promotion"

    review = build_review_report(
        _task("2元一包"),
        content="测试商品，只要2元一包。",
        revision=1,
        parsed=_parsed(decisions=[decision]),
    )

    assert review["decisions"][0]["action"] == "allow"
    assert review["publication_conclusion"] == "safe_to_publish"
    assert review["human_confirmation_items"] == []


def test_draft_added_promotion_condition_is_rewritten_not_confirmed() -> None:
    decision = _decision("confirm", matched_text="限时2元一包")
    decision["risk_family"] = "conditional_promotion"

    review = build_review_report(
        _task("2元一包"),
        content="测试商品，限时2元一包。",
        revision=1,
        parsed=_parsed(decisions=[decision], rewrite_mode="targeted"),
    )

    assert review["decisions"][0]["action"] == "rewrite"
    assert review["review_outcome"] == "needs_targeted_rewrite"
    assert review["human_confirmation_items"] == []


def test_user_provided_promotion_condition_remains_confirmation_eligible() -> None:
    decision = _decision("confirm", matched_text="限时2元一包")
    decision["risk_family"] = "conditional_promotion"

    review = build_review_report(
        _task("限时2元一包"),
        content="测试商品，限时2元一包。",
        revision=1,
        parsed=_parsed(decisions=[decision]),
    )

    assert review["decisions"][0]["action"] == "confirm"
    assert review["review_outcome"] == "needs_confirmation"


def test_changed_requirement_does_not_reuse_old_confirmation() -> None:
    task = _task("认证声明A")
    task.confirmed_evidence = [
        {
            "requirement_id": "req-1",
            "requirement_source_text": "认证声明A",
            "matched_text": "认证声明A",
            "risk_family": "qualification",
            "decision": "confirmed_with_basis",
        }
    ]
    task.requirements[0].source_text = "认证声明B"
    decision = _decision("confirm", matched_text="认证声明A")
    decision["risk_family"] = "qualification"

    review = build_review_report(
        task,
        content="测试商品认证声明A。",
        revision=2,
        parsed=_parsed(decisions=[decision]),
    )

    assert review["decisions"][0]["action"] == "confirm"


def test_parse_review_accepts_new_contract() -> None:
    model_payload = _model_payload(_parsed())
    content = "测试商品容量500ml。"
    parsed = parse_review(
        json.dumps(model_payload, ensure_ascii=False),
        {"req-1": "500ml"},
        content,
    )

    assert parsed["missing_requirement_ids"] == []
    assert parsed["decisions"] == []


def test_parse_review_accepts_valid_missing_requirement_id() -> None:
    payload = _model_payload(_parsed(status="missing"))

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        "测试商品。",
    )

    assert parsed["missing_requirement_ids"] == ["req-1"]


def test_parse_review_normalizes_irrelevant_rewrite_mode_without_retrying_semantics() -> None:
    payload = _model_payload(
        _parsed(decisions=[_decision("confirm")], rewrite_mode="targeted")
    )
    content = "测试商品容量500ml。"

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        content,
    )

    assert parsed["decisions"][0]["action"] == "confirm"
    assert parsed["rewrite_mode"] == "none"


def test_parse_review_normalizes_duplicate_and_unknown_coverage_references() -> None:
    payload = _model_payload(_parsed())
    payload["missing_requirement_ids"] = ["req-1", "req-1", "req-2"]

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        "测试商品。",
    )

    assert parsed["missing_requirement_ids"] == ["req-1"]


def test_parse_review_accepts_an_exact_quote_from_reviewed_content() -> None:
    payload = _model_payload(
        _parsed(
            decisions=[
                _decision(
                    "rewrite",
                    requirement_id="",
                    matched_text="容量约 500 ml",
                )
            ],
            rewrite_mode="targeted",
        )
    )
    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        "测试商品容量约 500 ml。",
    )

    assert parsed["decisions"][0]["matched_text"] == "容量约 500 ml"


def test_parse_review_rejects_a_quote_not_present_in_content_or_requirements() -> None:
    payload = _model_payload(_parsed(
        decisions=[_decision("rewrite", requirement_id="", matched_text="容量……")],
        rewrite_mode="targeted",
    ))

    with pytest.raises(ValueError, match="review_decision_quote_not_grounded"):
        parse_review(
            json.dumps(payload, ensure_ascii=False),
            {"req-1": "500ml"},
            "测试商品容量500ml。",
        )


def test_parse_review_grounds_an_omitted_risk_in_its_requirement() -> None:
    payload = _model_payload(_parsed(
        decisions=[_decision("rewrite", requirement_id="", matched_text="国产品牌第一")],
        rewrite_mode="targeted",
    ))

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "国产品牌第一"},
        "测试商品。",
    )

    assert parsed["decisions"][0]["requirement_id"] == "req-1"
    assert parsed["decisions"][0]["matched_text"] == "国产品牌第一"


def test_parse_review_repairs_unknown_requirement_id_from_matched_text() -> None:
    payload = _model_payload(_parsed(
        decisions=[_decision("rewrite", requirement_id="req-2")],
        rewrite_mode="targeted",
    ))

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        "测试商品容量500ml。",
    )

    assert parsed["decisions"][0]["requirement_id"] == "req-1"


def test_parse_review_attributes_an_extended_unprovided_claim_to_the_draft() -> None:
    decision = _decision("rewrite", requirement_id="req-1", matched_text="容量500ml")
    decision["risk_family"] = "unprovided_material_claim"
    payload = _model_payload(
        _parsed(decisions=[decision], rewrite_mode="targeted")
    )

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        "测试商品容量500ml。",
    )

    assert parsed["decisions"][0]["requirement_id"] == ""


def test_parse_review_discards_an_unprovided_claim_that_is_an_exact_requirement() -> None:
    decision = _decision("rewrite", requirement_id="req-1", matched_text="500ml")
    decision["risk_family"] = "unprovided_material_claim"
    payload = _model_payload(_parsed(decisions=[decision], rewrite_mode="targeted"))

    parsed = parse_review(
        json.dumps(payload, ensure_ascii=False),
        {"req-1": "500ml"},
        "测试商品容量500ml。",
    )

    assert parsed["decisions"] == []
    assert parsed["rewrite_mode"] == "none"


def test_parse_review_requires_confirmation_to_reference_requirement() -> None:
    payload = _model_payload(
        _parsed(decisions=[_decision("confirm", requirement_id="")])
    )

    with pytest.raises(ValueError, match="confirmation_requires_requirement_id"):
        parse_review(
            json.dumps(payload, ensure_ascii=False),
            {"req-1": "500ml"},
            "测试商品容量500ml。",
        )


def test_draft_contract_returns_copy() -> None:
    assert parse_draft('{"primary_draft":"测试商品"}') == {
        "primary_draft": "测试商品",
    }


def test_candidate_numeric_claims_must_be_grounded_in_task_facts() -> None:
    task = _task("60键位")

    with pytest.raises(ValueError, match="unprovided_numeric_claims:60%"):
        validate_numeric_claims('{"primary_draft":"60%紧凑布局"}', task)


def test_candidate_numeric_claims_accept_visible_product_text_only() -> None:
    task = _task("48h超强续航")
    task.image_analysis = {"visible_text": ["BEETLE TWS EARPHONE 680", "i10-T"]}

    parsed = validate_numeric_claims(
        '{"primary_draft":"包装可见 BEETLE TWS EARPHONE 680 和 i10-T"}',
        task,
    )

    assert "680" in parsed["primary_draft"]
    with pytest.raises(ValueError, match="unprovided_numeric_claims:1"):
        validate_numeric_claims(
            '{"primary_draft":"包装可见 i10-T，私藏清单+1"}',
            task,
        )


def test_revision_numeric_claims_may_preserve_the_current_draft() -> None:
    task = _task("60键位")
    task.stage_revision("60%布局", source="agent_generated", instruction="")

    parsed = validate_numeric_claims(
        '{"primary_draft":"60%布局，调整语气"}',
        task,
        preserve_current_draft=True,
    )

    assert parsed["primary_draft"] == "60%布局，调整语气"


def test_review_prompt_contains_current_contract_and_image_grounding() -> None:
    content = "测试商品容量500ml。"
    task = _task()
    task.image_analysis = {
        "visible_text": ["包装可见卖点"],
        "visible_features": ["绿色包装"],
    }
    messages = review_messages(
        task,
        content,
        {},
        {},
    )
    message = messages[0].content

    assert "advisory|rewrite|block|confirm" in message
    assert "missing_requirement_ids" in message
    assert "包装可见卖点" in messages[1].content
    assert "绿色包装" in messages[1].content


def test_rewrite_prompt_receives_review_decisions() -> None:
    task = _task()
    task.stage_revision("测试商品国产品牌第一。", source="agent_generated", instruction="")
    review = {"decisions": [_decision("rewrite", matched_text="国产品牌第一")]}
    messages = rewrite_messages(task, review)

    assert "国产品牌第一" in messages[1].content


def test_full_redraft_receives_all_unresolved_candidate_review_constraints() -> None:
    task = _task()
    first = task.stage_revision("测试商品第一版。", source="agent_generated", instruction="")
    first.review = {"decisions": [_decision("rewrite", matched_text="第一版")]}
    second = task.stage_revision("测试商品第二版。", source="risk_optimized", instruction="")
    second.review = {"decisions": [_decision("rewrite", matched_text="第二版", requirement_id="")]}

    payload = draft_messages(task, mode="generation", instruction="完整重写")[1].content

    assert "第一版" in payload
    assert "第二版" in payload


def test_rewrite_tool_only_generates_candidate_and_leaves_success_to_review() -> None:
    task = _task()
    task.stage_revision("测试商品国产品牌第一。", source="agent_generated", instruction="")
    review = {"decisions": [_decision("rewrite", matched_text="国产品牌第一")]}
    provider = QueueModelProvider(
        [
            {"primary_draft": "测试商品国产品牌第一。"},
            {"primary_draft": "测试商品，介绍产品自身特点。"},
        ]
    )
    tool = RiskRewriteTool(ModelExecutionRunner(provider, model="rewrite-model"))

    outcome = tool.run(task, review=review)

    assert outcome.ok
    assert outcome.parsed == {
        "primary_draft": "测试商品国产品牌第一。",
    }
    assert len(provider.calls) == 1


def test_draft_tool_receives_removed_requirements_as_explicit_exclusions() -> None:
    task = _task()
    provider = QueueModelProvider(
        [
            {"primary_draft": "容量500ml。"},
            {"primary_draft": "测试商品容量500ml。"},
        ]
    )
    tool = DraftCopyTool(ModelExecutionRunner(provider, model="draft-model"))

    outcome = tool.run(
        task,
        mode="revision",
        instruction="删除旧要求",
        excluded_requirements=["无灯版"],
    )

    assert outcome.ok
    assert len(provider.calls) == 1
    assert "无灯版" in provider.calls[0]["messages"][1].content


def test_knowledge_context_is_bounded() -> None:
    value = {
        "issue_hits": [{"issue_type": str(index)} for index in range(10)],
        "evidence_segments": [{"segment_id": str(index), "content": "x" * 500} for index in range(10)],
    }
    compact = compact_knowledge(value)

    assert len(compact["issue_hits"]) == 6
    assert len(compact["evidence_segments"]) == 4
    assert len(compact["evidence_segments"][0]["content"]) == 320
