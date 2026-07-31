from backend.app.application import ReleaseAgentController
from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionRunner, RetryPolicy
from tests.fakes import QueueModelProvider


def _runner(responses) -> tuple[ReleaseAgentController, QueueModelProvider]:
    provider = QueueModelProvider(responses)
    runner = ModelExecutionRunner(
        provider,
        model="controller-model",
        retry_policy=RetryPolicy(network_max_attempts=1, parse_max_attempts=1),
    )
    return ReleaseAgentController(runner), provider


def test_initial_understanding_preserves_risky_user_expression_without_classifying_it() -> None:
    controller, provider = _runner(
        [
            {
                "summary": "首次任务已结构化",
                "task_updates": {
                    "product_name": "测试零食",
                    "product_category": "零食",
                    "platform": "xiaohongshu",
                    "objective": "种草推广",
                },
                "requirements": [
                    {"source_text": "比具体竞品更好吃", "kind": "content"}
                ],
                "instruction": "生成小红书宣传文案",
            }
        ]
    )

    outcome = controller.understand_initial_task(
        ReleaseTask(task_id="initial"),
        "我要在小红书宣传测试零食，比具体竞品更好吃",
    )

    assert outcome.ok
    assert outcome.parsed["requirement_mutations"] == [
        {
            "operation": "add",
            "source_text": "比具体竞品更好吃",
            "kind": "content",
        }
    ]


def test_initial_understanding_rejects_ungrounded_rewrite() -> None:
    controller, _ = _runner(
        [
            {
                "summary": "错误改写要求",
                "task_updates": {
                    "product_name": "测试零食",
                    "product_category": "零食",
                    "platform": "xiaohongshu",
                    "objective": "",
                },
                "requirements": [{"source_text": "口感更扎实", "kind": "selling_point"}],
                "instruction": "生成文案",
            }
        ]
    )

    outcome = controller.understand_initial_task(
        ReleaseTask(task_id="ungrounded"),
        "我要在小红书宣传测试零食，比具体竞品更好吃",
    )

    assert not outcome.ok
    assert outcome.error == "initial_requirement_not_grounded:口感更扎实"


def test_initial_understanding_uses_observed_platform_and_image_product_type() -> None:
    controller, provider = _runner([
        {
            "summary": "首次任务已理解",
            "task_updates": {},
            "requirements": [{"source_text": "文案自然简洁", "kind": "style"}],
            "instruction": "生成文案",
        }
    ])
    task = ReleaseTask(
        task_id="observed-context",
        image_analysis={"product_type": "马克杯"},
    )

    outcome = controller.understand_initial_task(
        task,
        "我要在小红书宣传实物图里的商品，文案自然简洁",
    )

    assert outcome.ok
    assert outcome.parsed["task_updates"]["platform"] == "xiaohongshu"
    assert outcome.parsed["task_updates"]["product_category"] == "马克杯"
    assert len(provider.calls) == 1


def test_turn_decision_replaces_requirement_with_grounded_remove_and_add() -> None:
    controller, _ = _runner(
        [
            {
                "intent": "revise",
                "summary": "替换要求",
                "task_updates": {},
                "remove_requirement_ids": [],
                "new_requirements": [
                    {
                        "source_text": "把轻量化设计改成语气更简洁",
                        "kind": "style",
                        "replaces_requirement_id": "req-1",
                    }
                ],
                "answer": "",
                "question": "",
                "confirmation_resolutions": [],
                "reactivate_requirement_ids": [],
            }
        ]
    )
    task = ReleaseTask(task_id="replace")
    task.add_requirement("轻量化设计", kind="selling_point")
    task.stage_revision("当前文案", source="agent_generated", instruction="")

    outcome = controller.decide_turn(task, "把轻量化设计改成语气更简洁")

    assert outcome.ok
    assert [item["operation"] for item in outcome.parsed["requirement_mutations"]] == [
        "remove",
        "add",
    ]


def test_read_only_intent_discards_model_proposed_requirement_mutations() -> None:
    controller, _ = _runner(
        [
            {
                "intent": "restore",
                "summary": "只恢复文案",
                "task_updates": {
                    "product_name": "不应覆盖商品",
                    "platform": "douyin",
                },
                "remove_requirement_ids": ["req-1"],
                "new_requirements": [{"source_text": "只恢复文案", "kind": "content"}],
                "answer": "",
                "question": "",
                "confirmation_resolutions": [],
                "reactivate_requirement_ids": [],
                "target_revision": 1,
            }
        ]
    )
    task = ReleaseTask(task_id="read-only")
    task.add_requirement("当前要求")
    first = task.stage_revision("第一版", source="agent_generated", instruction="")
    first.status = "superseded"
    second = task.stage_revision("第二版", source="agent_generated", instruction="")
    second.status = "accepted"

    outcome = controller.decide_turn(task, "只恢复文案")

    assert outcome.ok
    assert outcome.parsed["requirement_mutations"] == []
    assert outcome.parsed["target_revision"] == 1
    assert not any(outcome.parsed["task_updates"].values())


def test_turn_decision_rejects_unknown_requirement_removal() -> None:
    controller, _ = _runner(
        [
            {
                "intent": "revise",
                "summary": "删除要求",
                "task_updates": {},
                "remove_requirement_ids": ["req-99"],
                "new_requirements": [],
                "answer": "",
                "question": "",
                "confirmation_resolutions": [],
                "reactivate_requirement_ids": [],
            }
        ]
    )
    task = ReleaseTask(task_id="unknown")
    task.add_requirement("当前要求")
    task.stage_revision("当前文案", source="agent_generated", instruction="")

    outcome = controller.decide_turn(task, "删除当前要求")

    assert not outcome.ok
    assert outcome.error == "unknown_removed_requirement_id:['req-99']"


def test_controller_can_reactivate_a_confirmed_requirement_while_restoring_copy() -> None:
    controller, provider = _runner(
        [
            {
                "intent": "restore",
                "summary": "确认宣传事实并恢复文案",
                "task_updates": {},
                "remove_requirement_ids": [],
                "new_requirements": [],
                "reactivate_requirement_ids": ["req-1"],
                "confirmation_resolutions": [],
                "answer": "",
                "question": "",
                "target_revision": 1,
            }
        ]
    )
    task = ReleaseTask(task_id="reactivate")
    requirement = task.add_requirement("待核验宣传事实", kind="fact")
    assert requirement is not None
    requirement.status = "removed_for_compliance"
    first = task.stage_revision("包含待核验宣传事实的第一版", source="agent_generated", instruction="")
    first.status = "superseded"
    second = task.stage_revision("当前安全版本", source="risk_optimized", instruction="")
    second.status = "accepted"

    outcome = controller.decide_turn(task, "该宣传事实有依据，恢复第一版文案")

    assert outcome.ok
    assert outcome.parsed["intent"] == "restore"
    assert outcome.parsed["reactivate_requirement_ids"] == ["req-1"]
    context = provider.calls[0]["messages"][1].content
    assert '"requirements_removed_by_risk_optimization"' in context
    assert '"requirement_id": "req-1"' in context
    assert '"text": "待核验宣传事实"' in context


def test_turn_decision_rejects_action_unavailable_in_current_task_state() -> None:
    controller, provider = _runner(
        [
            {
                "intent": "review",
                "summary": "审核当前文案",
                "task_updates": {},
            }
        ]
    )
    task = ReleaseTask(
        task_id="bounded-actions",
        product_category="键盘",
        platform="xiaohongshu",
    )

    outcome = controller.decide_turn(task, "审核一下")

    assert not outcome.ok
    assert outcome.error == "action_not_available:review"
    context = provider.calls[0]["messages"][1].content
    assert '"available_actions": ["explain", "draft"]' in context
