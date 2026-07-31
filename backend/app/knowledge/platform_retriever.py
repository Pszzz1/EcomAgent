from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List
import re

from .ad_law_documents import PLATFORM_RESOURCE_ROOT
from .models import KnowledgeSegment, KnowledgeSourceError, RetrievalQuery
from .platform_documents import PLATFORM_LABELS, PlatformDocumentLoader, normalize_platform
from .search import rank_knowledge_segments


@dataclass(frozen=True)
class PlatformIssueDefinition:
    issue_type: str
    label: str
    risk_level: str
    terms: List[str]
    reason: str
    suggestion: str
    platform_scope: List[str]
    consequence: str


@dataclass(frozen=True)
class PlatformIssueHit:
    issue_type: str
    label: str
    risk_level: str
    span: str
    start: int
    end: int
    reason: str
    suggestion: str
    platform_consequence: str
    source: Dict[str, Any]
    policy: Dict[str, Any]
    confidence: float = 0.88

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlatformRetrievalResult:
    issue_hits: List[PlatformIssueHit]
    evidence_segments: List[KnowledgeSegment]
    warnings: List[str]
    platform: str
    platform_label: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "platform_label": self.platform_label,
            "issue_hits": [hit.to_dict() for hit in self.issue_hits],
            "evidence_segments": [segment.to_dict() for segment in self.evidence_segments],
            "warnings": list(self.warnings),
        }


ISSUE_DEFINITIONS = [
    PlatformIssueDefinition(
        issue_type="external_contact_diversion",
        label="站外联系方式/私域导流",
        risk_level="high",
        terms=[
            "微信",
            "VX",
            "V信",
            "薇信",
            "绿泡泡",
            "卫星",
            "QQ",
            "手机号",
            "电话",
            "邮箱",
            "加我",
            "加客服",
            "私加",
            "主页联系方式",
            "看主页",
            "主页有惊喜",
        ],
        reason="文案包含平台外联系方式或疑似私域导流表达，容易触发平台发布审核或交易安全规则。",
        suggestion="删除站外联系方式和规避表达，引导用户在平台官方页面、店铺或商品详情内了解信息。",
        platform_scope=["douyin", "kuaishou", "xiaohongshu"],
        consequence="可能被限流、下架、拦截评论/私信，严重时影响账号或店铺信用。",
    ),
    PlatformIssueDefinition(
        issue_type="private_transaction",
        label="私下交易/绕开平台交易",
        risk_level="high",
        terms=[
            "私下交易",
            "私下转账",
            "微信转账",
            "线下付款",
            "不走平台",
            "绕过平台",
            "直接打款",
            "先款后发",
            "货到付款",
            "私聊下单",
            "私信下单",
        ],
        reason="文案引导用户脱离平台交易链路，容易违反交易安全和消费者保护相关规则。",
        suggestion="改为在平台店铺、商品页或官方交易链路内完成咨询和下单。",
        platform_scope=["douyin", "kuaishou", "xiaohongshu"],
        consequence="可能导致内容下架、交易功能限制、账号处罚或消费者纠纷。",
    ),
    PlatformIssueDefinition(
        issue_type="engagement_bait",
        label="诱导互动/诱导私信",
        risk_level="medium",
        terms=[
            "评论领取",
            "评论区领取",
            "评论扣1",
            "扣1领取",
            "私信领取",
            "私信发链接",
            "点赞领取",
            "关注领取",
            "转发领取",
            "收藏领取",
            "三连领取",
        ],
        reason="文案以福利、资料或链接诱导评论、点赞、关注、转发或私信，容易被平台识别为违规营销互动。",
        suggestion="改为说明活动入口在平台官方页面或商品详情页，避免强诱导互动话术。",
        platform_scope=["douyin", "kuaishou", "xiaohongshu"],
        consequence="可能影响推荐分发、评论展示、私信功能或内容发布审核。",
    ),
    PlatformIssueDefinition(
        issue_type="third_party_platform_diversion",
        label="跨平台引流/竞品平台导流",
        risk_level="high",
        terms=[],
        reason="目标平台文案中出现引导去其他平台查看、交易或领取福利的表达。",
        suggestion="删除其他平台名称和跳转引导，改为平台内官方页面、店铺或商品详情承接。",
        platform_scope=["douyin", "kuaishou", "xiaohongshu"],
        consequence="可能被识别为站外引流或竞品平台导流，影响发布和流量分发。",
    ),
    PlatformIssueDefinition(
        issue_type="commercial_disclosure_risk",
        label="商业推广披露不足",
        risk_level="medium",
        terms=["自用推荐", "亲测好物", "真实分享", "不是广告", "无广", "恰饭", "种草"],
        reason="商业推广内容如果伪装成普通个人体验，可能触发平台对虚假种草或商业披露不足的审核。",
        suggestion="确保商业合作、带货或利益相关关系按平台要求清楚标识，不虚构个人体验。",
        platform_scope=["xiaohongshu"],
        consequence="可能被判定为违规种草、虚假营销或商业披露不足，影响笔记曝光和账号信用。",
    ),
]

OTHER_PLATFORM_TERMS = {
    "douyin": ["快手", "小红书", "淘宝", "天猫", "京东", "拼多多", "B站", "微博"],
    "kuaishou": ["抖音", "小红书", "淘宝", "天猫", "京东", "拼多多", "B站", "微博"],
    "xiaohongshu": ["抖音", "快手", "淘宝", "天猫", "京东", "拼多多", "B站", "微博"],
}

DIVERSION_PREFIXES = ("去", "到", "上", "前往", "打开", "搜索", "搜")
DIVERSION_SUFFIXES = ("搜索", "搜", "下单", "购买", "领取", "拍下")


class PlatformPolicyRetriever:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or PLATFORM_RESOURCE_ROOT

    def retrieve(self, content: str, platform: str, category: str = "general", limit: int = 8) -> PlatformRetrievalResult:
        normalized = normalize_platform(platform)
        load_result = _cached_load(str(self.root_dir), normalized)
        if load_result.warnings:
            raise KnowledgeSourceError("；".join(load_result.warnings))
        hits = self._find_issue_hits(content, normalized)
        segments = (
            self._rank_segments(content, normalized, category, hits, load_result.segments)[:limit]
            if hits
            else []
        )
        return PlatformRetrievalResult(
            issue_hits=hits,
            evidence_segments=segments,
            warnings=load_result.warnings,
            platform=normalized,
            platform_label=PLATFORM_LABELS.get(normalized, normalized),
        )

    def _find_issue_hits(self, content: str, platform: str) -> List[PlatformIssueHit]:
        hits: List[PlatformIssueHit] = []
        seen = set()
        for definition in ISSUE_DEFINITIONS:
            if platform not in definition.platform_scope:
                continue
            terms = definition.terms
            if definition.issue_type == "third_party_platform_diversion":
                terms = _third_party_platform_spans(content, platform)
            for term in sorted(terms, key=len, reverse=True):
                for match in re.finditer(re.escape(term), content, flags=re.IGNORECASE):
                    key = (definition.issue_type, match.start(), match.end())
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(
                        PlatformIssueHit(
                            issue_type=definition.issue_type,
                            label=definition.label,
                            risk_level=definition.risk_level,
                            span=content[match.start() : match.end()],
                            start=match.start(),
                            end=match.end(),
                            reason=definition.reason,
                            suggestion=definition.suggestion,
                            platform_consequence=definition.consequence,
                            source={
                                "detector": "platform_policy_retriever",
                                "rule_id": definition.issue_type,
                                "version": f"platform-policy-{platform}-v1",
                                "source_type": "platform_policy_document_index",
                                "platform": platform,
                                "priority": 15 if definition.risk_level == "high" else 35,
                            },
                            policy={
                                "risk_family": (
                                    "platform_hard"
                                    if definition.risk_level == "high"
                                    else "platform_revise"
                                ),
                                "direct_block": definition.risk_level == "high",
                                "source_strength": "platform_policy_retrieval",
                            },
                        )
                    )
        return sorted(hits, key=lambda item: (item.start, item.issue_type))

    def _rank_segments(
        self,
        content: str,
        platform: str,
        category: str,
        hits: List[PlatformIssueHit],
        segments: List[KnowledgeSegment],
    ) -> List[KnowledgeSegment]:
        issue_terms = list({hit.label for hit in hits} | {hit.issue_type for hit in hits} | {hit.span for hit in hits})
        query = RetrievalQuery(
            content=content,
            category=category,
            platform=platform,
            scope="platform_policy",
            issue_terms=issue_terms,
            boost_terms=["导流", "联系方式", "私信", "交易", "互动", "引流", "站外", "私下"],
        )
        return rank_knowledge_segments(
            segments,
            query,
            source_type_boosts={"platform_policy_docx": 1},
            category_boost=2,
            platform_boost=5,
            issue_term_boost=8,
        )


def _third_party_platform_spans(content: str, platform: str) -> List[str]:
    spans: List[str] = []
    for term in OTHER_PLATFORM_TERMS.get(platform, []):
        escaped = re.escape(term)
        prefix = "|".join(re.escape(value) for value in DIVERSION_PREFIXES)
        suffix = "|".join(re.escape(value) for value in DIVERSION_SUFFIXES)
        if re.search(rf"(?:{prefix})\s*{escaped}", content) or re.search(
            rf"{escaped}\s*(?:{suffix})",
            content,
        ):
            spans.append(term)
    return spans

@lru_cache(maxsize=8)
def _cached_load(root_dir: str, platform: str):
    return PlatformDocumentLoader(Path(root_dir)).load(platform)
