from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionRunner
from backend.app.infrastructure.modeling.messages import ModelMessage
from backend.app.infrastructure.modeling.structured_output.common import _loads
from backend.app.infrastructure.persistence import ImageAssetStore

from .release_tools import ToolOutcome


class ProductImageAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1)
    product_type: str = ""
    visible_features: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    dominant_colors: list[str] = Field(default_factory=list)
    preservation_constraints: list[str] = Field(default_factory=list)
    quality_level: Literal["usable", "repairable", "retake_required"]
    quality_issues: list[str] = Field(default_factory=list)


class ProductImageAnalysisTool:
    name = "analyze_product_image"

    def __init__(self, runner: ModelExecutionRunner, assets: ImageAssetStore) -> None:
        self.runner = runner
        self.assets = assets

    def run(self, task: ReleaseTask) -> ToolOutcome:
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
        payload = {
            "product": {"name": task.product_name, "category": task.product_category},
            "image_dimensions": {"width": source_asset.width, "height": source_asset.height},
            "quality_policy": {
                "usable": "商品和关键标识清楚",
                "repairable": "商品可可靠识别，但拍摄背景、光线、轻微模糊或透视需要改善",
                "retake_required": "主体残缺、严重模糊遮挡，或关键品牌和型号无法辨认",
            },
        }
        outcome = self.runner.complete_structured(
            node_name=self.name,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        "你是商品实物图事实分析工具。只记录图片中可以直接观察到的商品形态、颜色、结构、Logo、"
                        "包装文字、配件和图像质量，不得根据商品名称脑补材质、性能或效果。"
                        "不得因为字段长度或预设数量省略已经识别出的关键标识、型号、包装文字和结构。"
                        "只有商品身份无法可靠确认时才判 retake_required。preservation_constraints 列出后续视觉处理"
                        "不能改变的外形、颜色、标识、文字和配件。只返回 JSON：{summary,product_type,visible_features,"
                        "visible_text,dominant_colors,preservation_constraints,quality_level,quality_issues}。"
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=[
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:"
                                + source_asset.mime_type
                                + ";base64,"
                                + base64.b64encode(source_path.read_bytes()).decode("ascii")
                            },
                        },
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                    ],
                ),
            ],
            response_format={"type": "json_object"},
            parser=lambda content: ProductImageAnalysis.model_validate(_loads(content)),
        )
        return ToolOutcome.from_model(self.name, outcome)
