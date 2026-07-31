from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .ad_law_documents import PLATFORM_RESOURCE_ROOT, _clean_text, _looks_like_heading, _read_docx_paragraphs
from .models import KnowledgeSegment


PLATFORM_DOCUMENTS = {
    "douyin": "抖音平台规则.docx",
    "kuaishou": "快手平台规则.docx",
    "xiaohongshu": "小红书平台规则.docx",
}

PLATFORM_LABELS = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
}


@dataclass(frozen=True)
class PlatformDocumentLoadResult:
    segments: List[KnowledgeSegment]
    warnings: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "segments": [segment.to_dict() for segment in self.segments],
            "warnings": list(self.warnings),
        }


class PlatformDocumentLoader:
    """Loads the selected platform policy document into searchable segments."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or PLATFORM_RESOURCE_ROOT

    def load(self, platform: str) -> PlatformDocumentLoadResult:
        normalized = normalize_platform(platform)
        filename = PLATFORM_DOCUMENTS.get(normalized)
        if not filename:
            return PlatformDocumentLoadResult(
                segments=[],
                warnings=[f"暂不支持平台规则文档：{platform or '未选择平台'}"],
            )

        path = _resolve_platform_file(self.root_dir, filename)
        if not path.exists():
            return PlatformDocumentLoadResult(
                segments=[],
                warnings=[f"未找到{PLATFORM_LABELS[normalized]}平台规则文档：{filename}"],
            )

        segments: List[KnowledgeSegment] = []
        current_section = ""
        for index, paragraph in enumerate(_read_docx_paragraphs(path), start=1):
            text = _clean_text(paragraph)
            if not text:
                continue
            if _looks_like_heading(text):
                current_section = text
            segments.append(
                KnowledgeSegment(
                    segment_id=f"{normalized}-docx-{index}",
                    source_file=path.name,
                    source_type="platform_policy_docx",
                    title=current_section or f"{PLATFORM_LABELS[normalized]}平台规则",
                    section=current_section,
                    content=text,
                    categories=[normalized, "platform_policy", "general"],
                    scope="platform_policy",
                    platform=normalized,
                    metadata={
                        "paragraph_index": index,
                        "platform_label": PLATFORM_LABELS[normalized],
                        "document_kind": "platform_policy",
                    },
                )
            )
        return PlatformDocumentLoadResult(segments=segments, warnings=[])


def normalize_platform(platform: str) -> str:
    aliases = {
        "douyin": "douyin",
        "抖音": "douyin",
        "kuaishou": "kuaishou",
        "快手": "kuaishou",
        "xiaohongshu": "xiaohongshu",
        "小红书": "xiaohongshu",
        "red": "xiaohongshu",
    }
    return aliases.get(str(platform).strip().lower(), str(platform).strip())


def _resolve_platform_file(root_dir: Path, filename: str) -> Path:
    return root_dir / filename
