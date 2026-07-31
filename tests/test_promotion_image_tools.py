from io import BytesIO
from pathlib import Path

from PIL import Image

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionRunner
from backend.app.infrastructure.modeling import ImageGenerationResult
from backend.app.infrastructure.persistence import ImageAssetStore
from backend.app.tools import PromotionImageGenerationTool, PromotionImagePromptTool
from tests.fakes import QueueModelProvider


def _image_bytes(color: str = "white") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 96), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _task() -> ReleaseTask:
    task = ReleaseTask(
        task_id="poster-task",
        product_name="测试保温杯",
        product_category="保温杯",
        platform="xiaohongshu",
        image_analysis={
            "visible_text": ["FUGUANG"],
            "visible_features": ["黑色圆柱杯身"],
            "preservation_constraints": ["保留竖排 FUGUANG 标识"],
        },
    )
    task.add_requirement("500ml", kind="fact")
    task.add_requirement("24小时保温", kind="selling_point")
    task.stage_revision(
        "富光保温杯，500ml容量，24小时保温。",
        source="agent_generated",
        instruction="生成宣传文案",
    )
    return task


def test_prompt_tool_leaves_visual_design_to_image_model() -> None:
    provider = QueueModelProvider(
        [
            {
                "display_text": ["500ml", "24小时保温"],
            }
        ]
    )
    tool = PromotionImagePromptTool(ModelExecutionRunner(provider, model="work-model"))

    outcome = tool.run(_task(), instruction="")

    assert outcome.ok is True
    assert outcome.parsed["display_text"] == ["500ml", "24小时保温"]
    assert "500ml" in outcome.parsed["image_prompt"]
    assert "24小时保温" in outcome.parsed["image_prompt"]
    assert "自主完成" in outcome.parsed["image_prompt"]
    assert "不是排版模板" in outcome.parsed["image_prompt"]
    system_prompt = provider.calls[0]["messages"][0].content
    assert "不负责构图" in system_prompt
    assert "不能改写、组合、补充或创造新文案" in system_prompt
    user_context = provider.calls[0]["messages"][1].content
    assert "accepted_copy" not in user_context


def test_prompt_tool_rejects_text_not_in_user_input() -> None:
    provider = QueueModelProvider(
        [
            {"display_text": ["小个子也能驾驭"]},
            {"display_text": ["小个子也能驾驭"]},
        ]
    )
    tool = PromotionImagePromptTool(ModelExecutionRunner(provider, model="work-model"))

    outcome = tool.run(_task(), instruction="")

    assert outcome.ok is False
    assert "promotion_image_text_not_from_user_input" in outcome.error


class RecordingImageGenerator:
    model = "image-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, *, source_image: str, prompt: str, size: str) -> ImageGenerationResult:
        self.calls.append({"source_image": source_image, "prompt": prompt, "size": size})
        return ImageGenerationResult(
            ok=True,
            model=self.model,
            content=_image_bytes("black"),
            request_id="image-request",
        )


def test_generation_tool_uses_one_source_image_and_saves_result(tmp_path: Path) -> None:
    assets = ImageAssetStore(tmp_path / "runs.sqlite3", tmp_path / "assets")
    source = assets.save_source(
        "poster-task",
        filename="product.png",
        mime_type="image/png",
        content=_image_bytes(),
    )
    task = _task()
    task.source_image_asset_id = source.asset_id
    generator = RecordingImageGenerator()
    tool = PromotionImageGenerationTool(generator, assets)
    plan = {
        "display_text": ["500ml", "24小时保温"],
        "image_prompt": "自主设计宣传图，准确展示500ml和24小时保温。",
    }

    outcome = tool.run(task, plan=plan)

    assert outcome.ok is True
    assert len(generator.calls) == 1
    assert generator.calls[0]["source_image"].startswith("data:image/png;base64,")
    assert assets.get(outcome.parsed["asset_id"]).kind == "promotion_image"
