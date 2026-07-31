from __future__ import annotations

from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree
from typing import Iterable, List

from pypdf import PdfReader

from .models import DocumentLoadResult, KnowledgeSegment


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESOURCE_ROOT = PROJECT_ROOT / "resources"
LEGAL_RESOURCE_ROOT = RESOURCE_ROOT / "legal"
PLATFORM_RESOURCE_ROOT = RESOURCE_ROOT / "platforms"


class AdLawDocumentLoader:
    """Loads the two local ad-law knowledge sources into searchable segments."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or LEGAL_RESOURCE_ROOT

    def load(self) -> DocumentLoadResult:
        segments: List[KnowledgeSegment] = []
        warnings: List[str] = []
        docx_path = _resolve_resource_file(self.root_dir, "广告法禁用词.docx")
        pdf_path = _resolve_resource_file(self.root_dir, "广告法.pdf")

        if docx_path.exists():
            segments.extend(self._load_docx(docx_path))
        else:
            warnings.append(f"未找到广告法禁用词文档：{docx_path.name}")

        if pdf_path.exists():
            pdf_result = self._load_pdf(pdf_path)
            segments.extend(pdf_result.segments)
            warnings.extend(pdf_result.warnings)
        else:
            warnings.append(f"未找到广告法 PDF：{pdf_path.name}")

        return DocumentLoadResult(segments=segments, warnings=warnings)

    def _load_docx(self, path: Path) -> List[KnowledgeSegment]:
        paragraphs = _read_docx_paragraphs(path)
        segments: List[KnowledgeSegment] = []
        current_section = ""
        for index, paragraph in enumerate(paragraphs, start=1):
            text = _clean_text(paragraph)
            if not text:
                continue
            if _looks_like_heading(text):
                current_section = text
            segments.append(
                KnowledgeSegment(
                    segment_id=f"docx-{index}",
                    source_file=path.name,
                    source_type="prohibited_terms_docx",
                    title=current_section or "广告法禁用词",
                    section=current_section,
                    content=text,
                    categories=_infer_categories(text),
                    scope="ad_law",
                    metadata={"paragraph_index": index, "document_kind": "forbidden_terms"},
                )
            )
        return segments

    def _load_pdf(self, path: Path) -> DocumentLoadResult:
        segments: List[KnowledgeSegment] = []
        reader = PdfReader(str(path))
        for page_index, page in enumerate(reader.pages, start=1):
            text = _clean_text(page.extract_text() or "")
            if not text:
                continue
            for chunk_index, chunk in enumerate(_split_pdf_page(text), start=1):
                segments.append(
                    KnowledgeSegment(
                        segment_id=f"pdf-{page_index}-{chunk_index}",
                        source_file=path.name,
                        source_type="ad_law_pdf",
                        title=_first_article_title(chunk) or "中华人民共和国广告法",
                        content=chunk,
                        page=page_index,
                        categories=_infer_categories(chunk),
                        scope="ad_law",
                        metadata={
                            "page": page_index,
                            "chunk_index": chunk_index,
                            "document_kind": "ad_law",
                        },
                    )
                )
        return DocumentLoadResult(segments=segments, warnings=[])


def _read_docx_paragraphs(path: Path) -> List[str]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: List[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        joined = "".join(texts).strip()
        if joined:
            paragraphs.append(joined)
    return paragraphs


def _resolve_resource_file(root_dir: Path, filename: str) -> Path:
    return root_dir / filename


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_heading(text: str) -> bool:
    return bool(re.match(r"^[一二三四五六七八九十]+[．、.]", text)) or bool(re.match(r"^\d+\.", text))


def _split_pdf_page(text: str) -> Iterable[str]:
    parts = re.split(r"(?=第[一二三四五六七八九十百]+条)", text)
    chunks = [part.strip() for part in parts if part.strip()]
    if len(chunks) <= 1:
        return [text[:1200]]
    return chunks


def _first_article_title(text: str) -> str:
    match = re.search(r"(第[一二三四五六七八九十百]+条)", text)
    return match.group(1) if match else ""


def _infer_categories(text: str) -> List[str]:
    mapping = {
        "education": ["教育", "培训", "考试", "升学"],
        "finance": ["金融", "理财", "投资", "收益", "保本"],
        "real_estate": ["房地产", "房源", "学区房", "升值"],
        "food_beauty": ["食品", "美妆", "保健食品", "化妆品", "祛斑", "祛痘"],
        "medical_beauty": ["医疗美容", "医疗", "药品", "医院", "医生"],
        "general": ["绝对化", "虚假", "误导", "国家级", "最高级", "最佳"],
    }
    categories: List[str] = []
    for category, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            categories.append(category)
    return categories or ["general"]
