from io import BytesIO
from pathlib import Path

from PIL import Image

from backend.app.infrastructure.modeling import ImageGenerationResult
from backend.app.schemas import ReleaseTaskInput, ReleaseTaskTurnInput
from backend.app.services import ReleaseTaskAgentService
from tests.fakes import QueueModelProvider


def _initial(requirements: list[tuple[str, str]], *, instruction="生成宣传文案") -> dict:
    return {
        "summary": "首次任务已结构化",
        "task_updates": {
            "product_name": "测试商品",
            "product_category": "键盘",
            "platform": "xiaohongshu",
            "objective": "宣传商品",
        },
        "requirements": [
            {"source_text": text, "kind": kind} for text, kind in requirements
        ],
        "instruction": instruction,
    }


def _turn(*, intent="revise", message="增加要求", mutations=None, **values) -> dict:
    return {
        "intent": intent,
        "summary": "本轮要求已理解",
        "task_updates": values.get("task_updates", {}),
        "remove_requirement_ids": [],
        "new_requirements": [
            {"source_text": text, "kind": kind}
            for text, kind in (mutations or [])
        ],
        "answer": values.get("answer", ""),
        "question": values.get("question", ""),
        "confirmation_resolutions": values.get("confirmation_resolutions", []),
        "reactivate_requirement_ids": values.get("reactivate_requirement_ids", []),
        "target_revision": values.get("target_revision", 0),
        "revision_target": values.get("revision_target", ""),
    }


def _review(
    ids: list[str],
    expressions: dict[str, str],
    *,
    missing: set[str] | None = None,
    decisions: list[dict] | None = None,
    rewrite_mode="none",
    question="",
) -> dict:
    missing = missing or set()
    return {
        "rewrite_mode": rewrite_mode,
        "needs_more_context": bool(question),
        "question": question,
        "decisions": decisions or [],
        "missing_requirement_ids": sorted(missing),
    }


def _decision(
    action: str,
    matched_text: str,
    *,
    requirement_id="req-1",
    family="misleading_promotion",
    severity="high",
) -> dict:
    return {
        "requirement_id": requirement_id,
        "matched_text": matched_text,
        "label": "明确风险表达",
        "risk_family": family,
        "severity": severity,
        "action": action,
        "reason": "当前表达需要处置",
    }


def _service(
    tmp_path: Path,
    responses,
    *,
    image_generator=None,
) -> tuple[ReleaseTaskAgentService, QueueModelProvider]:
    provider = QueueModelProvider(responses)
    service = ReleaseTaskAgentService(
        db_path=tmp_path / "agent.sqlite3",
        model_provider=provider,
        image_generator=image_generator,
    )
    return service, provider


def _safe_task(tmp_path: Path, *, extra=None, image_generator=None, with_image=False):
    responses = [
        *([_image_analysis()] if with_image else []),
        _initial([("黑白两色", "fact"), ("到手99元", "fact")]),
        {"primary_draft": "测试商品有黑白两色，到手99元。"},
        _review(
            ["req-1", "req-2"],
            {"req-1": "黑白两色", "req-2": "到手99元"},
        ),
    ]
    responses.extend(extra or [])
    service, provider = _service(tmp_path, responses, image_generator=image_generator)
    record = service.create_release_task(
        ReleaseTaskInput(
            task_id="task-1",
            task_brief="我要在小红书宣传测试商品，黑白两色，到手99元",
        ),
        **(
            {
                "image_filename": "product.png",
                "image_content_type": "image/png",
                "image_content": _image_bytes(),
            }
            if with_image
            else {}
        ),
    )
    return service, provider, record


def test_runtime_models_do_not_cross_work_and_review_responsibilities(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, [])
    try:
        work_model = service.settings.llm_work_model
        review_model = service.settings.llm_review_model

        assert service.release_graph.agent_controller.runner.models == [work_model]
        assert service.release_graph.draft_tool.runner.models == [work_model]
        assert service.release_graph.risk_rewrite_tool.runner.models == [work_model]
        assert service.release_graph.review_tool.runner.models == [review_model]
        assert service.release_graph.image_analysis_tool.runner.models == [review_model]
    finally:
        service.close()


def test_normal_task_uses_controller_draft_review_and_accepts_revision(tmp_path: Path) -> None:
    service, provider, record = _safe_task(tmp_path)
    try:
        state = record.model_dump()["state"]
        assert record.model_dump()["phase"] == "draft_review_ready"
        assert state["current_review"]["publication_conclusion"] == "safe_to_publish"
        assert state["active_requirements"][0]["source_text"] == "黑白两色"
        assert len(provider.calls) == 3
    finally:
        service.close()


def test_review_result_drives_publication_route_without_second_model_call(
    tmp_path: Path,
) -> None:
    service, provider = _service(
        tmp_path,
        [
            _initial([("日常使用舒适", "selling_point")]),
            {"primary_draft": "测试商品，日常使用舒适。"},
            _review(
                ["req-1"],
                {"req-1": "日常使用舒适"},
                decisions=[
                    _decision(
                        "advisory",
                        "日常使用舒适",
                        severity="medium",
                    )
                ],
            ),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(
                task_id="ambiguous-review",
                task_brief="我要在小红书宣传测试商品，日常使用舒适",
            )
        )
        assert record.model_dump()["phase"] == "draft_review_ready"
        assert record.model_dump()["state"]["current_review"]["review_outcome"] == "safe"
        assert record.model_dump()["state"]["current_review"]["decisions"][0]["action"] == "advisory"
        assert len(provider.calls) == 3
    finally:
        service.close()


def test_confirmed_copy_generates_image_then_package(tmp_path: Path) -> None:
    generator = RecordingImageGenerator()
    service, provider, record = _safe_task(
        tmp_path,
        image_generator=generator,
        with_image=True,
        extra=[
            {
                "display_text": ["黑白两色", "到手99元"],
                "image_prompt": "自主设计宣传图，准确展示黑白两色和到手99元。",
            }
        ],
    )
    try:
        before = len(provider.calls)
        generated = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="generate-image",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        assert generated.model_dump()["phase"] == "promotion_image_review_ready"
        assert generated.model_dump()["state"]["promotion_image"]["status"] == "awaiting_user"
        assert len(provider.calls) == before + 1
        assert len(generator.calls) == 1

        result = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="confirm-image",
                expected_state_version=generated.model_dump()["state"]["state_version"],
            ),
        )
        package = result.model_dump()["state"]["final_release_package"]
        assert result.status == "completed"
        assert result.model_dump()["phase"] == "release_package_ready"
        assert package["final_copy"]
        assert package["promotion_image_asset_id"]
        assert package["promotion_image_text"] == ["黑白两色", "到手99元"]
    finally:
        service.close()


def test_read_only_turn_preserves_the_image_waiting_for_confirmation(tmp_path: Path) -> None:
    generator = RecordingImageGenerator()
    service, provider, record = _safe_task(
        tmp_path,
        image_generator=generator,
        with_image=True,
        extra=[
            {"display_text": ["黑白两色", "到手99元"]},
            _turn(
                intent="explain",
                answer="这是对当前宣传图的说明。",
                task_updates={"product_name": "测试商品", "platform": "xiaohongshu"},
            ),
        ],
    )
    try:
        generated = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="generate-before-question",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        image_id = generated.model_dump()["state"]["promotion_image"]["asset_id"]

        explained = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                message="这是什么意思？",
                turn_id="explain-image",
                expected_state_version=generated.model_dump()["state"]["state_version"],
            ),
        )

        assert explained.model_dump()["phase"] == "promotion_image_review_ready"
        assert explained.model_dump()["state"]["promotion_image"] == generated.model_dump()["state"]["promotion_image"]
        assert '"current_phase": "promotion_image_review_ready"' in provider.calls[-1]["messages"][1].content

        completed = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="confirm-existing-image",
                expected_state_version=explained.model_dump()["state"]["state_version"],
            ),
        )

        assert completed.model_dump()["phase"] == "release_package_ready"
        assert completed.model_dump()["state"]["final_release_package"]["promotion_image_asset_id"] == image_id
        assert len(generator.calls) == 1
    finally:
        service.close()


def test_image_feedback_regenerates_only_the_image(tmp_path: Path) -> None:
    generator = RecordingImageGenerator()
    feedback = "移除平台标识，整体视觉风格更偏科技"
    service, provider, record = _safe_task(
        tmp_path,
        image_generator=generator,
        with_image=True,
        extra=[
            {"display_text": ["黑白两色", "到手99元"], "image_prompt": "第一版宣传图"},
            _turn(intent="explain", answer="当前无法从任务状态核验图片中的未知文字。"),
            _turn(
                intent="revise_image",
                message=feedback,
                task_updates={"objective": "不应改变"},
                mutations=[("不应成为文案要求", "style")],
            ),
            {"display_text": ["黑白两色", "到手99元"], "image_prompt": "科技风宣传图"},
        ],
    )
    try:
        generated = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="generate-first-image",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        original_state = generated.model_dump()["state"]

        explained = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                message="图片里的陌生文字是什么意思？",
                turn_id="ask-about-image",
                expected_state_version=original_state["state_version"],
            ),
        )

        revised = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                message=feedback,
                turn_id="revise-image-only",
                expected_state_version=explained.model_dump()["state"]["state_version"],
            ),
        )
        state = revised.model_dump()["state"]

        assert revised.model_dump()["phase"] == "promotion_image_review_ready"
        assert state["current_revision"] == original_state["current_revision"]
        assert state["current_draft"] == original_state["current_draft"]
        assert state["active_requirements"] == original_state["active_requirements"]
        assert state["objective"] == original_state["objective"]
        assert state["promotion_image"]["instruction"] == feedback
        assert len(generator.calls) == 2
        assert explained.model_dump()["phase"] == "promotion_image_review_ready"
        assert '"current_phase": "promotion_image_review_ready"' in provider.calls[-2]["messages"][1].content
    finally:
        service.close()


def test_ambiguous_image_feedback_stays_in_image_phase_without_mutating_copy(
    tmp_path: Path,
) -> None:
    generator = RecordingImageGenerator()
    service, _, record = _safe_task(
        tmp_path,
        image_generator=generator,
        with_image=True,
        extra=[
            {"display_text": ["黑白两色", "到手99元"], "image_prompt": "第一版宣传图"},
            _turn(
                intent="clarify",
                question="请说明要修改商品事实还是宣传图画面。",
            ),
        ],
    )
    try:
        generated = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="generate-image-before-clarify",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        before = generated.model_dump()

        clarified = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                message="商品细节发生变化",
                turn_id="clarify-image-feedback",
                expected_state_version=before["state"]["state_version"],
            ),
        )

        after = clarified.model_dump()
        assert after["phase"] == "promotion_image_review_ready"
        assert after["state"]["current_revision"] == before["state"]["current_revision"]
        assert after["state"]["current_draft"] == before["state"]["current_draft"]
        assert after["state"]["promotion_image"] == before["state"]["promotion_image"]
        assert len(generator.calls) == 1
    finally:
        service.close()


def test_image_feedback_contract_rejects_copy_revision_and_recovers_to_image_revision(
    tmp_path: Path,
) -> None:
    generator = RecordingImageGenerator()
    feedback = "增加一些机械感"
    service, _, record = _safe_task(
        tmp_path,
        image_generator=generator,
        with_image=True,
        extra=[
            {"display_text": ["黑白两色", "到手99元"], "image_prompt": "第一版宣传图"},
            _turn(intent="revise", message=feedback, mutations=[(feedback, "style")]),
            _turn(
                intent="revise_image",
                message=feedback,
                revision_target="image",
            ),
            {"display_text": ["黑白两色", "到手99元"], "image_prompt": "机械感宣传图"},
        ],
    )
    try:
        generated = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                turn_id="generate-image-before-contract-repair",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        before = generated.model_dump()

        revised = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                message=feedback,
                turn_id="revise-image-after-contract-repair",
                expected_state_version=before["state"]["state_version"],
            ),
        )

        state = revised.model_dump()["state"]
        assert revised.model_dump()["phase"] == "promotion_image_review_ready"
        assert state["current_revision"] == before["state"]["current_revision"]
        assert state["current_draft"] == before["state"]["current_draft"]
        assert state["active_requirements"] == before["state"]["active_requirements"]
        assert state["promotion_image"]["instruction"] == feedback
        assert len(generator.calls) == 2
    finally:
        service.close()


def _image_analysis() -> dict:
    return {
        "summary": "商品主体清晰。",
        "product_type": "键盘",
        "visible_features": ["黑色主体"],
        "visible_text": ["TEST"],
        "dominant_colors": ["黑色"],
        "preservation_constraints": ["保留正面标识"],
        "quality_level": "usable",
        "quality_issues": [],
    }


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingImageGenerator:
    model = "image-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, *, source_image: str, prompt: str, size: str) -> ImageGenerationResult:
        self.calls.append({"source_image": source_image, "prompt": prompt, "size": size})
        return ImageGenerationResult(
            ok=True,
            model=self.model,
            content=_image_bytes(),
            request_id="generated-image",
        )


def test_missing_requirement_gets_one_correction_then_review(tmp_path: Path) -> None:
    service, provider = _service(
        tmp_path,
        [
            _initial([("黑白两色", "fact"), ("到手99元", "fact")]),
            {"primary_draft": "测试商品有黑白两色。"},
            _review(
                ["req-1", "req-2"],
                {"req-1": "黑白两色", "req-2": ""},
                missing={"req-2"},
            ),
            {"primary_draft": "测试商品有黑白两色，到手99元。"},
            _review(
                ["req-1", "req-2"],
                {"req-1": "黑白两色", "req-2": "到手99元"},
            ),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(
                task_id="repair",
                task_brief="我要在小红书宣传测试商品，黑白两色，到手99元",
            )
        )
        assert record.model_dump()["phase"] == "draft_review_ready"
        assert record.model_dump()["state"]["current_review"]["unfulfilled_requirement_ids"] == []
        assert len(provider.calls) == 5
    finally:
        service.close()


def test_repeated_requirement_omission_keeps_candidate_for_user_guidance(
    tmp_path: Path,
) -> None:
    service, provider = _service(
        tmp_path,
        [
            _initial([("黑白两色", "fact"), ("到手99元", "fact")]),
            {"primary_draft": "测试商品有黑白两色。"},
            _review(
                ["req-1", "req-2"],
                {"req-1": "黑白两色", "req-2": "到手99元"},
                missing={"req-2"},
            ),
            {"primary_draft": "测试商品仍提供黑白两色。"},
            _review(
                ["req-1", "req-2"],
                {"req-1": "黑白两色", "req-2": "到手99元"},
                missing={"req-2"},
            ),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(
                task_id="repair-not-converged",
                task_brief="我要在小红书宣传测试商品，黑白两色，到手99元",
            )
        )

        state = record.model_dump()["state"]
        assert record.model_dump()["phase"] == "draft_revision_needed"
        assert state["current_draft"] == "测试商品仍提供黑白两色。"
        assert state["current_review"]["unfulfilled_requirement_ids"] == ["req-2"]
        assert state["active_requirements"][1]["source_text"] == "到手99元"
        assert len(provider.calls) == 5
    finally:
        service.close()


def test_risk_rewrite_is_reviewed_again_and_does_not_reintroduce_original_requirement(
    tmp_path: Path,
) -> None:
    risk = "国产品牌第一"
    service, provider = _service(
        tmp_path,
        [
            _initial([(risk, "content")]),
            {"primary_draft": f"测试商品，{risk}。"},
            _review(
                ["req-1"],
                {"req-1": risk},
                decisions=[_decision("rewrite", risk)],
                rewrite_mode="targeted",
            ),
            {"primary_draft": "测试商品，介绍产品自身特点。"},
            _review([], {}),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(
                task_id="risk",
                task_brief=f"我要在小红书宣传测试商品，{risk}",
            )
        )
        state = record.model_dump()["state"]
        assert record.model_dump()["phase"] == "draft_review_ready"
        assert state["current_draft"] == "测试商品，介绍产品自身特点。"
        assert state["requirements"][0]["source_text"] == risk
        assert state["requirements"][0]["status"] == "removed_for_compliance"
        assert state["active_requirements"] == []
        assert "违规点" in record.model_dump()["answer"]
        assert len(provider.calls) == 5
    finally:
        service.close()


def test_nonconverging_risk_rewrite_waits_for_user_without_another_model_cycle(
    tmp_path: Path,
) -> None:
    original_risk = "国产品牌第一"
    generated_risk = "所有人都说好"
    service, provider = _service(
        tmp_path,
        [
            _initial([(original_risk, "content")]),
            {"primary_draft": f"测试商品，{original_risk}。"},
            _review(
                ["req-1"],
                {"req-1": original_risk},
                decisions=[_decision("rewrite", original_risk)],
                rewrite_mode="targeted",
            ),
            {"primary_draft": f"测试商品，{generated_risk}。"},
            _review(
                [],
                {},
                decisions=[
                    _decision("rewrite", generated_risk, requirement_id="")
                ],
                rewrite_mode="targeted",
            ),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(
                task_id="full-redraft",
                task_brief=f"我要在小红书宣传测试商品，{original_risk}",
            )
        )
        state = record.model_dump()["state"]
        assert record.model_dump()["phase"] == "draft_revision_needed"
        assert state["current_draft"] == f"测试商品，{generated_risk}。"
        assert state["current_review"]["publication_conclusion"] == "revise_before_publish"
        assert state["requirements"][0]["status"] == "active"
        assert "复审后仍有必须处理的表达" in record.model_dump()["answer"]
        assert len(provider.calls) == 5
    finally:
        service.close()


def test_human_confirmation_is_resolved_without_another_model_call(tmp_path: Path) -> None:
    claim = "获得权威认证"
    confirmation = _decision("confirm", claim, family="qualification")
    service, provider = _service(
        tmp_path,
        [
            _initial([(claim, "fact")]),
            {"primary_draft": f"测试商品{claim}。"},
            _review(
                ["req-1"],
                {"req-1": claim},
                decisions=[confirmation],
            ),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(task_id="confirm", task_brief=f"我要在小红书宣传测试商品，{claim}")
        )
        before = len(provider.calls)
        result = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                confirmation_resolutions=[
                    {
                        "decision_id": "decision-1-1",
                        "resolution": "confirmed_with_basis",
                        "evidence_notes": "有真实认证材料",
                    }
                ],
                turn_id="confirm-1",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        state = result.model_dump()["state"]
        assert state["current_review"]["publication_conclusion"] == "safe_to_publish"
        assert state["confirmed_evidence"][0]["requirement_id"] == "req-1"
        assert len(provider.calls) == before
    finally:
        service.close()


def test_accepting_confirmation_risk_rewrites_once_and_closes_confirmation(tmp_path: Path) -> None:
    claim = "限时活动"
    confirmation = _decision("confirm", claim, family="conditional_promotion")
    service, provider = _service(
        tmp_path,
        [
            _initial([(claim, "content")]),
            {"primary_draft": f"测试商品{claim}。"},
            _review(["req-1"], {"req-1": claim}, decisions=[confirmation]),
            {"primary_draft": "测试商品，介绍产品。"},
            _review([], {}),
        ],
    )
    try:
        record = service.create_release_task(
            ReleaseTaskInput(task_id="accept-risk", task_brief=f"我要在小红书宣传测试商品，{claim}")
        )
        result = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                confirmation_resolutions=[
                    {
                        "decision_id": "decision-1-1",
                        "resolution": "rewrite_without_basis",
                    }
                ],
                turn_id="accept-risk-1",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )
        assert result.model_dump()["phase"] == "draft_review_ready"
        assert not result.model_dump()["state"]["pending_confirmation"]
        assert len(provider.calls) == 5
    finally:
        service.close()


def test_mixed_confirmation_resolutions_retain_only_the_confirmed_requirement(
    tmp_path: Path,
) -> None:
    retained_claim = "认证信息"
    rewritten_claim = "限时活动"
    service, provider = _service(
        tmp_path,
        [
            _initial([(retained_claim, "fact"), (rewritten_claim, "content")]),
            {"primary_draft": f"测试商品{retained_claim}，{rewritten_claim}。"},
            _review(
                ["req-1", "req-2"],
                {"req-1": retained_claim, "req-2": rewritten_claim},
                decisions=[
                    _decision("confirm", retained_claim, family="qualification"),
                    _decision(
                        "confirm",
                        rewritten_claim,
                        requirement_id="req-2",
                        family="conditional_promotion",
                    ),
                ],
            ),
            {"primary_draft": f"测试商品{retained_claim}。"},
            _review(["req-1"], {"req-1": retained_claim}),
        ],
    )
    try:
        first = service.create_release_task(
            ReleaseTaskInput(
                task_id="mixed-confirmation",
                task_brief=f"我要在小红书宣传测试商品，{retained_claim}，{rewritten_claim}",
            )
        )
        result = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                confirmation_resolutions=[
                    {
                        "decision_id": "decision-1-1",
                        "resolution": "confirmed_with_basis",
                        "evidence_notes": "已有可核验材料",
                    },
                    {
                        "decision_id": "decision-1-2",
                        "resolution": "rewrite_without_basis",
                    }
                ],
                turn_id="resolve-mixed-confirmations",
                expected_state_version=first.model_dump()["state"]["state_version"],
            ),
        )

        state = result.model_dump()["state"]
        requirements = {item["requirement_id"]: item for item in state["requirements"]}
        assert result.model_dump()["phase"] == "draft_review_ready"
        assert not state["pending_confirmation"]
        assert requirements["req-1"]["status"] == "active"
        assert requirements["req-2"]["status"] == "removed_for_compliance"
        assert [item["requirement_id"] for item in state["confirmed_evidence"]] == ["req-1"]
        assert len(provider.calls) == 5
    finally:
        service.close()


def test_confirming_a_removed_requirement_can_restore_copy_without_restoring_old_state(
    tmp_path: Path,
) -> None:
    claim = "认证信息"
    service, provider = _service(
        tmp_path,
        [
            _initial([(claim, "fact")]),
            {"primary_draft": f"测试商品{claim}。"},
            _review(
                ["req-1"],
                {"req-1": claim},
                decisions=[_decision("confirm", claim, family="qualification")],
            ),
            {"primary_draft": "测试商品。"},
            _review([], {}),
            _turn(
                intent="restore",
                target_revision=99,
                reactivate_requirement_ids=["req-1"],
            ),
            _turn(
                intent="restore",
                target_revision=1,
                reactivate_requirement_ids=["req-1"],
            ),
            _review(
                ["req-1"],
                {"req-1": claim},
                decisions=[_decision("confirm", claim, family="qualification")],
            ),
        ],
    )
    try:
        first = service.create_release_task(
            ReleaseTaskInput(
                task_id="confirm-after-rewrite",
                task_brief=f"我要在小红书宣传测试商品，{claim}",
            )
        )
        optimized = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                confirmation_resolutions=[
                    {
                        "decision_id": "decision-1-1",
                        "resolution": "rewrite_without_basis",
                    }
                ],
                turn_id="rewrite-unconfirmed-claim",
                expected_state_version=first.model_dump()["state"]["state_version"],
            ),
        )
        failed_restore = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                message="这项宣传事实有真实依据，我确认并恢复原文案",
                turn_id="confirm-and-restore-missing-copy",
                expected_state_version=optimized.model_dump()["state"]["state_version"],
            ),
        )
        assert failed_restore.model_dump()["phase"] == "draft_review_ready"
        assert failed_restore.model_dump()["state"]["requirements"][0]["status"] == "removed_for_compliance"
        assert failed_restore.model_dump()["state"]["revisions"] == optimized.model_dump()["state"]["revisions"]

        restored = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                message="这项宣传事实有真实依据，我确认并恢复原文案",
                turn_id="confirm-and-restore-copy",
                expected_state_version=failed_restore.model_dump()["state"]["state_version"],
            ),
        )

        state = restored.model_dump()["state"]
        assert restored.model_dump()["phase"] == "draft_review_ready"
        assert state["current_draft"] == first.model_dump()["state"]["current_draft"]
        assert state["draft_origin"] == "restored"
        assert state["requirements"][0]["status"] == "active"
        assert state["confirmed_evidence"][0]["requirement_id"] == "req-1"
        assert state["confirmed_evidence"][0]["comment"] == "这项宣传事实有真实依据，我确认并恢复原文案"
        assert state["current_review"]["publication_conclusion"] == "safe_to_publish"
        assert len(provider.calls) == 8
    finally:
        service.close()


def test_explanation_is_read_only_and_uses_only_controller(tmp_path: Path) -> None:
    answer = "当前审核没有要求修改。"
    service, provider, first = _safe_task(
        tmp_path,
        extra=[_turn(intent="explain", answer=answer)],
    )
    try:
        revision = first.model_dump()["state"]["current_revision"]
        result = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                message="怎么了？",
                turn_id="explain-1",
                expected_state_version=first.model_dump()["state"]["state_version"],
            ),
        )
        assert result.model_dump()["phase"] == "draft_review_ready"
        assert result.status == "waiting_user"
        assert result.model_dump()["answer"] == answer
        assert result.model_dump()["state"]["current_revision"] == revision
        assert len(provider.calls) == 4
    finally:
        service.close()


def test_restore_keeps_exact_previous_copy_without_repairing_later_requirements(
    tmp_path: Path,
) -> None:
    service, _, first = _safe_task(
        tmp_path,
        extra=[
            _turn(mutations=[("语气更活泼", "style")]),
            {"primary_draft": "活泼版测试商品有黑白两色，到手99元。"},
            _review(
                ["req-1", "req-2", "req-3"],
                {"req-1": "黑白两色", "req-2": "到手99元", "req-3": "活泼版"},
            ),
            _turn(intent="restore", target_revision=1),
            _review(
                ["req-1", "req-2", "req-3"],
                {"req-1": "黑白两色", "req-2": "到手99元", "req-3": ""},
                missing={"req-3"},
            ),
        ],
    )
    try:
        revised = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                message="语气更活泼",
                turn_id="revise-before-restore",
                expected_state_version=first.model_dump()["state"]["state_version"],
            ),
        )
        restored = service.continue_release_task(
            first.task_id,
            ReleaseTaskTurnInput(
                message="恢复到版本 v1 的文案",
                turn_id="restore-copy",
                expected_state_version=revised.model_dump()["state"]["state_version"],
            ),
        )

        state = restored.model_dump()["state"]
        assert state["current_draft"] == first.model_dump()["state"]["current_draft"]
        assert state["draft_origin"] == "restored"
        assert state["active_requirements"][-1]["source_text"] == "语气更活泼"
        assert restored.phase == "draft_revision_needed"
        assert state["current_review"]["unfulfilled_requirement_ids"] == ["req-3"]
        assert "req-3" in restored.answer
    finally:
        service.close()


def test_restore_unknown_revision_returns_a_user_response_without_mutation(
    tmp_path: Path,
) -> None:
    service, provider, record = _safe_task(
        tmp_path,
        extra=[_turn(intent="restore", target_revision=99)],
    )
    try:
        result = service.continue_release_task(
            record.task_id,
            ReleaseTaskTurnInput(
                message="恢复到版本 v99",
                turn_id="restore-missing-version",
                expected_state_version=record.model_dump()["state"]["state_version"],
            ),
        )

        assert result.model_dump()["phase"] == "draft_review_ready"
        assert result.model_dump()["answer"] == "当前不存在版本 v99。"
        assert result.model_dump()["state"]["revisions"] == record.model_dump()["state"]["revisions"]
        assert len(provider.calls) == 4
    finally:
        service.close()


def test_delete_removes_checkpoint_task(tmp_path: Path) -> None:
    service, _, record = _safe_task(tmp_path)
    try:
        service.delete_release_task(record.task_id)
        assert service.get_task(record.task_id) is None
        assert all(
            snapshot.thread_id != record.task_id
            for snapshot in service.checkpointer.latest_thread_states()
        )
        assert service.release_graph.snapshot(record.task_id) == {}
    finally:
        service.close()


def test_history_summary_is_derived_from_the_latest_checkpoint(tmp_path: Path) -> None:
    service, _, result = _safe_task(tmp_path)
    try:
        summaries = service.list_tasks()

        assert len(summaries) == 1
        assert summaries[0].task_id == result.task_id
        assert summaries[0].phase == result.phase
        assert summaries[0].current_revision == result.state.current_revision
        assert summaries[0].updated_at
    finally:
        service.close()
