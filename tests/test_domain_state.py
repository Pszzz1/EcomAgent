import pytest

from backend.app.domain import ReleaseTask, STATE_SCHEMA_VERSION


def _safe_review(revision: int, content: str) -> dict:
    return {
        "revision": revision,
        "content": content,
        "publication_conclusion": "safe_to_publish",
        "readiness_score": 100,
        "decisions": [],
        "unfulfilled_requirement_ids": [],
    }


def test_state_rejects_incompatible_schema() -> None:
    with pytest.raises(ValueError, match="unsupported state schema"):
        ReleaseTask.from_snapshot("task-1", {"schema_version": STATE_SCHEMA_VERSION - 1})


def test_requirements_have_stable_ids_and_review_is_owned_by_revision() -> None:
    task = ReleaseTask(task_id="task-1", platform="douyin", product_name="测试商品")
    first = task.add_requirement("黑白两种配色", kind="fact")
    duplicate = task.add_requirement("黑白两种配色", kind="fact")
    assert first is duplicate
    assert first is not None and first.requirement_id == "req-1"

    revision = task.stage_revision(
        "测试商品有黑白两种配色。",
        source="agent_generated",
        instruction="生成文案",
    )
    task.record_review(revision.revision, _safe_review(revision.revision, revision.content))
    task.accept_revision(revision.revision)

    restored = ReleaseTask.from_snapshot(task.task_id, task.to_snapshot())
    assert restored.current_draft == revision.content
    assert restored.current_review["unfulfilled_requirement_ids"] == []
    assert restored.current_revision_record is not None
    assert restored.current_revision_record.status == "accepted"
    assert "review" in restored.current_revision_record.to_dict()


def test_checkpoint_snapshot_excludes_public_derived_fields() -> None:
    task = ReleaseTask(task_id="minimal-snapshot", product_name="测试商品")
    task.add_requirement("黑白两种配色", kind="selling_point")
    revision = task.stage_revision("测试文案", source="agent_generated", instruction="生成")
    task.record_review(revision.revision, _safe_review(revision.revision, revision.content))

    snapshot = task.to_snapshot()
    public_state = task.to_public_state()

    assert "active_requirements" not in snapshot
    assert "current_draft" not in snapshot
    assert "review_history" not in snapshot
    assert public_state["active_requirements"][0]["requirement_id"] == "req-1"
    assert public_state["current_draft"] == "测试文案"


def test_review_must_match_revision_content() -> None:
    task = ReleaseTask(task_id="task-1")
    revision = task.stage_revision("原文", source="user_provided", instruction="")

    with pytest.raises(ValueError, match="does not match"):
        task.record_review(
            revision.revision,
            {"content": "其他文案", "publication_conclusion": "safe_to_publish"},
        )


def test_restore_only_restores_copy_and_waits_for_fresh_review() -> None:
    task = ReleaseTask(task_id="restore-copy")
    task.add_requirement("第一项要求")
    first = task.stage_revision("第一版文案", source="agent_generated", instruction="生成")
    task.record_review(first.revision, _safe_review(first.revision, first.content))
    task.accept_revision(first.revision)

    task.add_requirement("后来新增要求")
    second = task.stage_revision("第二版文案", source="agent_revised", instruction="修改")
    task.record_review(second.revision, _safe_review(second.revision, second.content))
    task.accept_revision(second.revision)

    restored = task.restore_revision()

    assert restored is not None
    assert restored.content == first.content
    assert restored.source == "restored"
    assert restored.review == {}
    assert [item.text for item in task.active_requirements] == ["第一项要求", "后来新增要求"]


def test_accepting_revision_rejects_other_candidates_from_the_same_turn() -> None:
    task = ReleaseTask(task_id="candidate-cleanup")
    first = task.stage_revision("风险候选", source="agent_generated", instruction="生成")
    second = task.stage_revision("安全候选", source="risk_optimized", instruction="改写")
    task.record_review(second.revision, _safe_review(second.revision, second.content))

    task.accept_revision(second.revision)

    assert first.status == "rejected"
    assert second.status == "accepted"
