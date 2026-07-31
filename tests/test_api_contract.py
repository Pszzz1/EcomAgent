from fastapi.testclient import TestClient
from io import BytesIO
from pathlib import Path

from PIL import Image

from backend.app.domain import ReleaseTask
from backend.app.main import app, create_app
from backend.app.schemas import ReleaseTaskResult, ReleaseTaskSummary
from backend.app.services import ReleaseTaskConflictError
from backend.app.tools.review_policy import build_review_report


def _result(task_id: str = "task-1") -> ReleaseTaskResult:
    task = ReleaseTask(
        task_id=task_id,
        task_brief="创建任务",
        product_name="测试商品",
        product_category="键盘",
        platform="xiaohongshu",
    )
    revision = task.stage_revision(
        "测试商品发布文案。",
        source="agent_generated",
        instruction="生成文案",
    )
    review = build_review_report(
        task,
        content=revision.content,
        revision=revision.revision,
        parsed={
            "summary": "当前文案安全",
            "rewrite_mode": "none",
            "needs_more_context": False,
            "question": "",
            "decisions": [],
            "missing_requirement_ids": [],
        },
    )
    task.record_review(revision.revision, review)
    task.accept_revision(revision.revision)
    return ReleaseTaskResult(
        task_id=task_id,
        status="waiting_user",
        phase="draft_review_ready",
        answer="当前稿已完成审核",
        next_questions=[],
        state=task.to_public_state(),
        trace_events=[],
    )


class ServiceStub:
    def __init__(self) -> None:
        self.result = _result()
        self.updated_at = "2026-07-30T00:00:00+00:00"
        self.create_error: Exception | None = None

    def create_release_task(self, item, **image):
        if self.create_error:
            raise self.create_error
        return self.result

    def continue_release_task(self, task_id, user_input):
        if task_id != self.result.task_id:
            raise KeyError(task_id)
        return self.result

    def replace_product_image(self, task_id, **image):
        if task_id != self.result.task_id:
            raise KeyError(task_id)
        return self.result

    def get_task(self, task_id):
        return self.result if self.result and task_id == self.result.task_id else None

    def list_tasks(self):
        if self.result is None:
            return []
        return [
            ReleaseTaskSummary(
                task_id=self.result.task_id,
                status=self.result.status,
                phase=self.result.phase,
                product_name=self.result.state.product_name,
                product_category=self.result.state.product_category,
                platform=self.result.state.platform,
                current_revision=self.result.state.current_revision,
                updated_at=self.updated_at,
            )
        ]

    def delete_release_task(self, task_id):
        if self.result is None or task_id != self.result.task_id:
            raise KeyError(task_id)
        self.result = None


def test_public_imports_and_release_routes_exist() -> None:
    paths = set(app.openapi()["paths"])

    assert "/release-tasks" in paths
    assert "/release-tasks/{task_id}/continue" in paths
    assert "/release-tasks/{task_id}/product-image" in paths
    assert "/health" in paths
    assert ReleaseTaskResult.model_fields["state"]
    assert issubclass(ReleaseTaskConflictError, RuntimeError)


def test_built_react_workspace_is_served_at_root(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    with TestClient(create_app(service=ServiceStub(), frontend_dist=tmp_path)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_release_api_returns_public_results_and_summary() -> None:
    service = ServiceStub()
    with TestClient(create_app(service=service)) as client:
        created = client.post(
            "/release-tasks",
            data={"task_brief": "创建任务"},
            files={"product_image": ("product.png", _png_bytes(), "image/png")},
        )
        fetched = client.get("/release-tasks/task-1")
        listed = client.get("/release-tasks")

    assert created.status_code == 200
    assert fetched.json() == created.json()
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "task_id": "task-1",
            "status": "waiting_user",
            "phase": "draft_review_ready",
            "product_name": "测试商品",
            "product_category": "键盘",
            "platform": "xiaohongshu",
            "current_revision": 1,
            "updated_at": service.updated_at,
        }
    ]
    assert "input_payload" not in listed.json()[0]


def test_release_api_uses_stable_not_found_and_conflict_errors() -> None:
    service = ServiceStub()
    with TestClient(create_app(service=service)) as client:
        missing = client.get("/release-tasks/missing")
        missing_turn = client.post("/release-tasks/missing/continue", json={"message": "继续"})
        service.create_error = ReleaseTaskConflictError("task id conflict")
        conflict = client.post(
            "/release-tasks",
            data={"task_brief": "创建任务"},
            files={"product_image": ("product.png", _png_bytes(), "image/png")},
        )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "task_not_found"
    assert missing_turn.status_code == 404
    assert missing_turn.json()["detail"]["code"] == "task_not_found"
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "task_conflict",
        "message": "task id conflict",
    }


def test_release_api_permanently_deletes_a_task() -> None:
    service = ServiceStub()
    with TestClient(create_app(service=service)) as client:
        deleted = client.delete("/release-tasks/task-1")
        missing = client.delete("/release-tasks/task-1")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "task_not_found"


def test_release_api_replaces_the_product_image_inside_the_same_task() -> None:
    service = ServiceStub()
    with TestClient(create_app(service=service)) as client:
        response = client.post(
            "/release-tasks/task-1/product-image",
            data={"expected_state_version": "1"},
            files={"product_image": ("replacement.png", _png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-1"


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), "white").save(buffer, format="PNG")
    return buffer.getvalue()
