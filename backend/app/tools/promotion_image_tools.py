from __future__ import annotations

import base64
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionRunner
from backend.app.infrastructure.modeling import DashScopeImageGenerator, ModelMessage
from backend.app.infrastructure.modeling.structured_output.common import _loads
from backend.app.infrastructure.persistence import ImageAssetStore

from .release_tools import ToolOutcome


class PromotionImageTextSelection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_text: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize_display_text(self) -> "PromotionImageTextSelection":
        self.display_text = list(
            dict.fromkeys(text.strip() for text in self.display_text if text.strip())
        )
        if not self.display_text:
            raise ValueError("promotion_image_text_is_empty")
        return self


class PromotionImagePromptTool:
    name = "plan_promotion_image"

    def __init__(self, runner: ModelExecutionRunner) -> None:
        self.runner = runner

    def run(self, task: ReleaseTask, *, instruction: str) -> ToolOutcome:
        text_candidates = _text_candidates(task)
        context = {
            "product": {"name": task.product_name, "category": task.product_category},
            "platform": task.platform,
            "text_candidates": text_candidates,
            "user_image_feedback": instruction.strip(),
        }
        outcome = self.runner.complete_structured(
            node_name=self.name,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        "你只负责选择宣传图中要展示的文字，不负责构图、场景、配色、光影或排版。"
                        "display_text 的每一项必须从 text_candidates 的 text 中原样截取，不能改写、组合、补充或创造新文案。"
                        "优先选择品牌、核心卖点、规格、价格等适合在图片中展示的内容；数量由用户提供的内容决定，"
                        "不要因为画面空间自行遗漏用户明确提供的重要信息。"
                        "user_image_feedback 只用于理解用户希望强调或弱化哪些候选文字，不能成为新增文字来源。"
                        "只返回 JSON：{display_text}。"
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=json.dumps(context, ensure_ascii=False),
                ),
            ],
            response_format={"type": "json_object"},
            parser=lambda content: _validate_text_selection(
                PromotionImageTextSelection.model_validate(_loads(content)).display_text,
                text_candidates,
            ),
        )
        result = ToolOutcome.from_model(self.name, outcome)
        if result.ok:
            result.parsed["image_prompt"] = _image_prompt(
                task,
                display_text=list(result.parsed["display_text"]),
                instruction=instruction,
            )
        return result


class PromotionImageGenerationTool:
    name = "generate_promotion_image"

    def __init__(
        self,
        generator: DashScopeImageGenerator,
        assets: ImageAssetStore,
    ) -> None:
        self.generator = generator
        self.assets = assets

    def run(self, task: ReleaseTask, *, plan: dict) -> ToolOutcome:
        source_path = self.assets.path_for(task.source_image_asset_id)
        source_asset = self.assets.get(task.source_image_asset_id)
        if source_path is None or source_asset is None:
            return ToolOutcome(
                ok=False,
                tool_name=self.name,
                error="Source product image is unavailable.",
                error_type="image_asset_missing",
                result_type="technical_failure",
            )
        result = self.generator.generate(
            source_image=(
                f"data:{source_asset.mime_type};base64,"
                + base64.b64encode(source_path.read_bytes()).decode("ascii")
            ),
            prompt=str(plan["image_prompt"]),
            size=_platform_size(task.platform),
        )
        attempt = {
            "provider": "dashscope",
            "model": result.model,
            "status": "ok" if result.ok else "failed",
            "latency_ms": result.latency_ms,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error": result.error,
            "request_id": result.request_id,
        }
        if not result.ok:
            return ToolOutcome(
                ok=False,
                tool_name=self.name,
                attempts=[attempt],
                error=result.error,
                error_type="image_generation_failed",
                result_type="technical_failure",
            )
        asset = self.assets.save_generated(
            task.task_id,
            content=result.content,
            mime_type=result.mime_type,
            metadata={
                "model": result.model,
                "request_id": result.request_id,
                "copy_revision": task.current_revision,
            },
        )
        return ToolOutcome(
            ok=True,
            tool_name=self.name,
            attempts=[attempt],
            parsed={
                "asset_id": asset.asset_id,
                "display_text": list(plan["display_text"]),
                "image_prompt": str(plan["image_prompt"]),
            },
        )


def _platform_size(platform: str) -> str:
    return "1536*2048" if platform == "xiaohongshu" else "1152*2048"


def _text_candidates(task: ReleaseTask) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if task.product_name.strip():
        candidates.append({"source": "product_name", "text": task.product_name.strip()})
    for requirement in task.active_requirements:
        source = requirement.source_text.strip()
        if source:
            candidates.append(
                {"source": requirement.requirement_id, "text": source}
            )
    return candidates


def _validate_text_selection(
    display_text: list[str], text_candidates: list[dict[str, str]]
) -> dict[str, list[str]]:
    sources = [item["text"] for item in text_candidates]
    invented = [text for text in display_text if not any(text in source for source in sources)]
    if invented:
        raise ValueError("promotion_image_text_not_from_user_input:" + "|".join(invented))
    return {"display_text": display_text}


def _image_prompt(
    task: ReleaseTask, *, display_text: list[str], instruction: str
) -> str:
    platform = {
        "douyin": "抖音",
        "kuaishou": "快手",
        "xiaohongshu": "小红书",
    }.get(task.platform, task.platform)
    required_text = "\n".join(f"- {text}" for text in display_text)
    feedback = instruction.strip()
    return (
        f"参考上传的干净背景商品图，为{platform}制作一张能够吸引顾客的一体化电商宣传图。"
        "以图中商品为唯一商品主体，保留商品身份、主要外观、当前主视角、Logo 和包装文字；"
        "可以为整体画面自然调整光影和轻微透视，但不要改成虚构的新角度或新款式。"
        "准确融入以下宣传文字，不改写、不补充新的宣传事实：\n"
        f"{required_text}\n"
        "这些文字是需要表达的信息，不是排版模板。请自主完成成熟购物平台水准的创意场景、构图、"
        "光影、配色、视觉层级和文字设计，让商品与文字自然形成一个完整画面，"
        "不要做成白底商品图旁边排列标签，也不要机械堆叠整段文案。"
        "除商品原有文字外，不要添加其他宣传内容。"
        + (f"用户对本次图片的调整要求：{feedback}" if feedback else "")
    )
