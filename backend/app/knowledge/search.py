from __future__ import annotations

import re
from typing import Dict, Iterable, List

from .models import KnowledgeSegment, RetrievalQuery, ScoredKnowledgeSegment


def rank_knowledge_segments(
    segments: Iterable[KnowledgeSegment],
    query: RetrievalQuery,
    *,
    source_type_boosts: Dict[str, int] | None = None,
    category_boost: int = 6,
    platform_boost: int = 5,
    issue_term_boost: int = 8,
    token_boost: int = 2,
    boost_term_boost: int = 2,
) -> List[KnowledgeSegment]:
    scored: List[ScoredKnowledgeSegment] = []
    query_tokens = set(tokenize(query.content))
    source_type_boosts = source_type_boosts or {}

    for segment in segments:
        score = 0
        reasons: List[str] = []
        categories = set(segment.categories or [])

        if query.scope and segment.scope == query.scope:
            score += 3
            reasons.append(f"scope:{query.scope}")
        if query.category and query.category in categories:
            score += category_boost
            reasons.append(f"category:{query.category}")
        if "general" in categories:
            score += 2
            reasons.append("category:general")
        if query.platform and (segment.platform == query.platform or query.platform in categories):
            score += platform_boost
            reasons.append(f"platform:{query.platform}")

        for term in _unique(query.issue_terms):
            if term and term in segment.content:
                score += issue_term_boost
                reasons.append(f"issue_term:{term}")

        for token in query_tokens:
            if len(token) >= 2 and token in segment.content:
                score += token_boost
                reasons.append(f"query_token:{token}")

        for term in _unique(query.boost_terms):
            if term and term in segment.content:
                score += boost_term_boost
                reasons.append(f"boost_term:{term}")

        source_boost = source_type_boosts.get(segment.source_type, 0)
        if source_boost:
            score += source_boost
            reasons.append(f"source_type:{segment.source_type}")

        if score > 0:
            scored.append(ScoredKnowledgeSegment(segment=segment, score=score, reasons=_unique(reasons)))

    scored.sort(key=lambda item: (item.score, _source_priority(item.segment)), reverse=True)
    return [item.as_evidence() for item in scored]


def tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9.%]+", text)


def _source_priority(segment: KnowledgeSegment) -> int:
    if segment.source_type.endswith("_docx"):
        return 2
    if segment.source_type.endswith("_pdf"):
        return 1
    return 0


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
