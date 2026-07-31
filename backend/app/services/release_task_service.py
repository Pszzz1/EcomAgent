from __future__ import annotations

from hashlib import sha1
import json
from pathlib import Path
from uuid import uuid4

from backend.app.application import ReleaseAgentController, ReleasePackageBuilder
from backend.app.domain import ReleaseTask, STATE_SCHEMA_VERSION
from backend.app.infrastructure.execution import ModelExecutionRunner, ModelHealthRegistry
from backend.app.infrastructure.modeling import (
    DashScopeImageGenerator,
    ModelProvider,
    build_model_provider,
)
from backend.app.infrastructure.persistence import (
    ImageAsset,
    ImageAssetStore,
    SQLiteCheckpointSaver,
)
from backend.app.infrastructure.settings import load_settings
from backend.app.orchestration.release_graph import ReleaseAgentGraph
from backend.app.schemas import (
    ReleaseTaskInput,
    ReleaseTaskResult,
    ReleaseTaskSummary,
    ReleaseTaskTurnInput,
)
from backend.app.tools import (
    CandidateReviewTool,
    DraftCopyTool,
    ProductImageAnalysisTool,
    PromotionImageGenerationTool,
    PromotionImagePromptTool,
    RiskRewriteTool,
)


class ReleaseTaskConflictError(RuntimeError):
    """A stale, duplicate, or concurrent turn cannot mutate the task."""


class ReleaseTaskAgentService:
    """Public facade for one durable release-task Agent lifecycle."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        model_provider: ModelProvider | None = None,
        image_asset_store: ImageAssetStore | None = None,
        image_generator: DashScopeImageGenerator | None = None,
    ) -> None:
        self.settings = load_settings()
        self.db_path = Path(db_path or self.settings.task_db_path)
        self.model_provider = model_provider or build_model_provider()
        self.checkpointer = SQLiteCheckpointSaver(self.db_path)
        self.image_asset_store = image_asset_store or ImageAssetStore(
            self.db_path,
            self.settings.image_asset_path,
        )
        model_health = ModelHealthRegistry()
        agent_runner = ModelExecutionRunner(
            self.model_provider,
            model=self.settings.llm_work_model,
            health_registry=model_health,
        )
        review_runner = ModelExecutionRunner(
            self.model_provider,
            model=self.settings.llm_review_model,
            health_registry=model_health,
        )
        draft_runner = ModelExecutionRunner(
            self.model_provider,
            model=self.settings.llm_work_model,
            health_registry=model_health,
        )
        rewrite_runner = ModelExecutionRunner(
            self.model_provider,
            model=self.settings.llm_work_model,
            health_registry=model_health,
        )
        visual_runner = ModelExecutionRunner(
            self.model_provider,
            model=self.settings.llm_review_model,
            health_registry=model_health,
        )
        image_generator = image_generator or DashScopeImageGenerator(
            api_key=self.settings.dashscope_api_key,
            model=self.settings.image_generation_model,
            base_url=self.settings.dashscope_native_base_url,
            timeout_seconds=max(180.0, self.settings.llm_timeout_seconds),
        )
        self.release_graph = ReleaseAgentGraph(
            agent_controller=ReleaseAgentController(agent_runner),
            draft_tool=DraftCopyTool(draft_runner),
            review_tool=CandidateReviewTool(review_runner),
            risk_rewrite_tool=RiskRewriteTool(rewrite_runner),
            image_analysis_tool=ProductImageAnalysisTool(
                visual_runner,
                self.image_asset_store,
            ),
            promotion_image_prompt_tool=PromotionImagePromptTool(draft_runner),
            promotion_image_generation_tool=PromotionImageGenerationTool(
                image_generator,
                self.image_asset_store,
            ),
            package_builder=ReleasePackageBuilder(),
            checkpointer=self.checkpointer,
        )

    def create_release_task(
        self,
        item: ReleaseTaskInput,
        *,
        image_filename: str = "",
        image_content_type: str = "",
        image_content: bytes = b"",
    ) -> ReleaseTaskResult:
        task_id = item.task_id or f"release-{uuid4().hex}"
        fingerprint_payload = item.model_dump()
        if image_content:
            fingerprint_payload["source_image_sha1"] = sha1(image_content).hexdigest()
        fingerprint = _request_fingerprint(fingerprint_payload)
        with self.checkpointer.task_lease(task_id):
            existing = self.release_graph.snapshot(task_id)
            if existing:
                task_state = dict(existing.get("task", {}))
                if int(task_state.get("schema_version", 0) or 0) != STATE_SCHEMA_VERSION:
                    raise ReleaseTaskConflictError(
                        f"Release task id belongs to an incompatible task state: {task_id}"
                    )
                if str(existing.get("last_completed_turn_fingerprint", "")) != fingerprint:
                    raise ReleaseTaskConflictError(
                        f"Release task id was already created with different input: {task_id}"
                    )
                return self._result(existing)

            created_image = False
            try:
                if image_content:
                    asset = self.image_asset_store.save_source(
                        task_id,
                        filename=image_filename,
                        mime_type=image_content_type,
                        content=image_content,
                    )
                    item = item.model_copy(
                        update={"task_id": task_id, "source_image_asset_id": asset.asset_id}
                    )
                    created_image = True
                task = ReleaseTask.from_input(task_id, item)
                state = self.release_graph.invoke(
                    {
                        "task_id": task_id,
                        "task": task.to_snapshot(),
                        "input_payload": item.model_dump(),
                        "user_message": item.task_brief.strip(),
                        "confirmation_resolutions": [],
                        "turn_id": f"create::{task_id}",
                        "turn_kind": "create",
                        "turn_fingerprint": fingerprint,
                        "traces": [],
                    },
                    task_id=task_id,
                )
            except Exception:
                if created_image and not self.release_graph.snapshot(task_id):
                    self.image_asset_store.delete_task(task_id)
                raise
            return self._result(state)

    def continue_release_task(
        self,
        task_id: str,
        user_input: ReleaseTaskTurnInput,
    ) -> ReleaseTaskResult:
        turn_id = user_input.turn_id.strip() or f"turn-{uuid4().hex}"
        fingerprint = _request_fingerprint(
            {
                "message": user_input.message,
                "confirmation_resolutions": [
                    item.model_dump() for item in user_input.confirmation_resolutions
                ],
            }
        )
        with self.checkpointer.task_lease(task_id):
            existing = self.release_graph.snapshot(task_id)
            if not existing:
                raise KeyError(f"Release task not found: {task_id}")
            task = ReleaseTask.from_snapshot(task_id, dict(existing.get("task", {})))
            if str(existing.get("last_completed_turn_id", "")) == turn_id:
                if str(existing.get("last_completed_turn_fingerprint", "")) != fingerprint:
                    raise ReleaseTaskConflictError(
                        f"Turn id was already used with different input: {turn_id}"
                    )
                return self._result(existing)
            expected = user_input.expected_state_version
            if expected is not None and expected != task.state_version:
                raise ReleaseTaskConflictError(
                    f"Task version changed: expected {expected}, current {task.state_version}"
                )
            state = self.release_graph.invoke(
                {
                    "user_message": user_input.message,
                    "confirmation_resolutions": [
                        item.model_dump() for item in user_input.confirmation_resolutions
                    ],
                    "turn_id": turn_id,
                    "turn_kind": "conversation",
                    "turn_fingerprint": fingerprint,
                },
                task_id=task_id,
            )
            return self._result(state)

    def replace_product_image(
        self,
        task_id: str,
        *,
        image_filename: str,
        image_content_type: str,
        image_content: bytes,
        expected_state_version: int | None = None,
    ) -> ReleaseTaskResult:
        with self.checkpointer.task_lease(task_id):
            existing = self.release_graph.snapshot(task_id)
            if not existing:
                raise KeyError(f"Release task not found: {task_id}")
            task = ReleaseTask.from_snapshot(task_id, dict(existing.get("task", {})))
            if expected_state_version is not None and expected_state_version != task.state_version:
                raise ReleaseTaskConflictError(
                    f"Task version changed: expected {expected_state_version}, current {task.state_version}"
                )
            old_asset_id = task.source_image_asset_id
            replacement = self.image_asset_store.save_source(
                task_id,
                filename=image_filename,
                mime_type=image_content_type,
                content=image_content,
            )
            task.replace_source_image(replacement.asset_id)
            input_payload = {
                **dict(existing.get("input_payload", {})),
                "source_image_asset_id": replacement.asset_id,
            }
            try:
                state = self.release_graph.invoke(
                    {
                        "task": task.to_snapshot(),
                        "input_payload": input_payload,
                        "user_message": "",
                        "confirmation_resolutions": [],
                        "turn_id": f"replace-image::{uuid4().hex}",
                        "turn_kind": "replace_image",
                        "turn_fingerprint": sha1(image_content).hexdigest(),
                    },
                    task_id=task_id,
                )
            except Exception:
                snapshot = self.release_graph.snapshot(task_id)
                committed_task = dict(snapshot.get("task", {})) if snapshot else {}
                if committed_task.get("source_image_asset_id") == replacement.asset_id:
                    self.image_asset_store.delete_assets([old_asset_id])
                else:
                    self.image_asset_store.delete_assets([replacement.asset_id])
                raise
            self.image_asset_store.delete_assets([old_asset_id])
            return self._result(state)

    def get_task(self, task_id: str) -> ReleaseTaskResult | None:
        state = self.release_graph.snapshot(task_id)
        if not state:
            return None
        task_state = dict(state.get("task", {}))
        if int(task_state.get("schema_version", 0) or 0) != STATE_SCHEMA_VERSION:
            return None
        return self._result(state)

    def list_tasks(self) -> list[ReleaseTaskSummary]:
        summaries: list[ReleaseTaskSummary] = []
        for snapshot in self.checkpointer.latest_thread_states():
            task_state = dict(snapshot.values.get("task", {}))
            if int(task_state.get("schema_version", 0) or 0) != STATE_SCHEMA_VERSION:
                continue
            result = self._result(snapshot.values)
            summaries.append(
                ReleaseTaskSummary(
                    task_id=result.task_id,
                    status=result.status,
                    phase=result.phase,
                    product_name=result.state.product_name,
                    product_category=result.state.product_category,
                    platform=result.state.platform,
                    current_revision=result.state.current_revision,
                    updated_at=snapshot.updated_at,
                )
            )
        return summaries

    def delete_release_task(self, task_id: str) -> None:
        with self.checkpointer.task_lease(task_id):
            if not self.release_graph.snapshot(task_id):
                raise KeyError(f"Release task not found: {task_id}")
            self.checkpointer.delete_thread(task_id)
            self.image_asset_store.delete_task(task_id)

    def get_image_asset(self, task_id: str, asset_id: str) -> tuple[ImageAsset, str]:
        asset = self.image_asset_store.get(asset_id)
        path = self.image_asset_store.path_for(asset_id)
        if asset is None or asset.task_id != task_id or path is None:
            raise KeyError(asset_id)
        return asset, str(path)

    def close(self) -> None:
        self.checkpointer.close()

    def _result(self, graph_state: dict) -> ReleaseTaskResult:
        task_id = str(graph_state["task_id"])
        task = ReleaseTask.from_snapshot(task_id, dict(graph_state.get("task", {})))
        return ReleaseTaskResult(
            task_id=task_id,
            status=str(graph_state.get("status", "waiting_user")),
            phase=str(graph_state.get("phase", "turn_not_applied")),
            answer=str(graph_state.get("answer", "")),
            next_questions=[str(item) for item in graph_state.get("next_questions", [])],
            state=task.to_public_state(),
            trace_events=[
                dict(item) for item in graph_state.get("traces", []) if isinstance(item, dict)
            ],
        )


def _request_fingerprint(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha1(payload.encode("utf-8")).hexdigest()
