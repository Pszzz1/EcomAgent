from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List


class KnowledgeSourceError(RuntimeError):
    """Raised when a required review knowledge source cannot be loaded."""


@dataclass(frozen=True)
class KnowledgeSegment:
    segment_id: str
    source_file: str
    source_type: str
    title: str
    content: str
    section: str = ""
    page: int | None = None
    categories: List[str] | None = None
    scope: str = "general"
    platform: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def with_retrieval(self, score: int, reasons: List[str]) -> "KnowledgeSegment":
        metadata = dict(self.metadata)
        metadata["retrieval_score"] = score
        metadata["retrieval_reasons"] = list(reasons)
        return replace(self, metadata=metadata)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["categories"] = list(self.categories or [])
        data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class DocumentLoadResult:
    segments: List[KnowledgeSegment]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segments": [segment.to_dict() for segment in self.segments],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RetrievalQuery:
    content: str
    category: str = "general"
    platform: str = ""
    scope: str = "general"
    issue_terms: List[str] = field(default_factory=list)
    boost_terms: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoredKnowledgeSegment:
    segment: KnowledgeSegment
    score: int
    reasons: List[str]

    def as_evidence(self) -> KnowledgeSegment:
        return self.segment.with_retrieval(self.score, self.reasons)
