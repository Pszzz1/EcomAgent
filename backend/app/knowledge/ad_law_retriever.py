from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List
import re

from .ad_law_documents import AdLawDocumentLoader, LEGAL_RESOURCE_ROOT
from .models import KnowledgeSegment, KnowledgeSourceError, RetrievalQuery
from .search import rank_knowledge_segments


CATEGORY_ALIASES = {
    "food": "food_beauty",
    "beauty": "food_beauty",
    "healthcare": "medical_beauty",
    "medical": "medical_beauty",
    "education": "education",
    "finance": "finance",
    "real_estate": "real_estate",
    "apparel": "general",
    "home": "general",
    "mother_baby_pet": "general",
    "electronics": "general",
    "other": "general",
    "general": "general",
}


@dataclass(frozen=True)
class AdLawIssueDefinition:
    issue_type: str
    label: str
    risk_level: str
    terms: List[str]
    reason: str
    suggestion: str
    replacement: str
    category_scope: List[str]


@dataclass(frozen=True)
class AdLawIssueHit:
    issue_type: str
    label: str
    risk_level: str
    span: str
    start: int
    end: int
    reason: str
    suggestion: str
    replacement: str
    source: Dict[str, Any]
    confidence: float = 0.86

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdLawRetrievalResult:
    issue_hits: List[AdLawIssueHit]
    evidence_segments: List[KnowledgeSegment]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_hits": [hit.to_dict() for hit in self.issue_hits],
            "evidence_segments": [segment.to_dict() for segment in self.evidence_segments],
            "warnings": list(self.warnings),
        }


ISSUE_DEFINITIONS = [
    AdLawIssueDefinition(
        issue_type="absolute_superlative",
        label="绝对化用语",
        risk_level="high",
        terms=[
            "最佳",
            "最优",
            "最好",
            "最大",
            "最高",
            "最低",
            "最流行",
            "最受欢迎",
            "最先进",
            "极致",
            "终极",
            "顶级",
            "顶尖",
            "极品",
            "至尊",
            "独一无二",
            "史无前例",
            "万能",
            "无敌",
            "最高级",
            "国家级",
            "全网最低",
            "中国第一",
            "全网第一",
            "销量第一",
            "排名第一",
            "NO.1",
            "TOP.1",
            "全球第一",
            "全国第一",
            "唯一",
        ],
        reason="命中广告宣传中的绝对化或难以证明表达，容易被认定为夸大宣传。",
        suggestion="改成可证明、相对、客观的描述，避免最高级、唯一性或第一类断言。",
        replacement="较受关注",
        category_scope=["general"],
    ),
    AdLawIssueDefinition(
        issue_type="misleading_promotion",
        label="误导性促销/焦虑营销",
        risk_level="medium",
        terms=["再不抢就没了", "错过再等一年", "万人疯抢", "秒杀", "清仓", "仅此一次", "最后一波", "限时最后一天"],
        reason="命中制造紧迫感或促销条件不清的表达，可能引发价格、库存或活动真实性争议。",
        suggestion="补充真实活动条件，弱化焦虑式催促，说明以页面规则为准。",
        replacement="活动期间优惠中",
        category_scope=["general"],
    ),
    AdLawIssueDefinition(
        issue_type="medicalized_claim",
        label="医疗化用语",
        risk_level="high",
        terms=[
            "防癌",
            "抗癌",
            "降血压",
            "防治高血压",
            "祛疤",
            "消炎",
            "杀菌",
            "提高免疫力",
            "助眠",
            "失眠",
            "补肾",
            "生发",
            "镇定",
            "镇静",
            "活血",
            "补血",
            "安神",
            "排毒养颜",
            "医生推荐",
            "医院同款",
            "临床验证",
        ],
        reason="普通商品不得涉及疾病治疗功能或使用容易与医疗、药品混淆的表达。",
        suggestion="删除医疗、治疗、疾病和医生背书类表述，改为普通使用体验或客观属性。",
        replacement="使用体验因人而异",
        category_scope=["general", "food_beauty", "medical_beauty"],
    ),
    AdLawIssueDefinition(
        issue_type="false_commitment",
        label="虚假承诺/保证性承诺",
        risk_level="high",
        terms=[
            "100%有效",
            "100%安全",
            "无效退款",
            "零风险",
            "包治百病",
            "永不反弹",
            "永久有效",
            "特效",
            "纯天然",
            "无毒副作用",
            "保证见效",
            "一次见效",
            "永久",
        ],
        reason="命中无法稳定证明或带保证性质的承诺，可能构成虚假或引人误解的宣传。",
        suggestion="删除确定性保证，改为有限定条件的客观说明。",
        replacement="实际效果因人而异",
        category_scope=["general"],
    ),
    AdLawIssueDefinition(
        issue_type="unauthorized_endorsement",
        label="资质/背书证明不足",
        risk_level="medium",
        terms=["权威认证", "官方认证", "国家认证", "专利技术", "国家机关推荐", "政府指定", "专供", "特供"],
        reason="资质、认证、专利、官方背书类表述需要真实材料支撑，否则存在证明不足风险。",
        suggestion="仅在有真实证明材料时保留，并补充准确来源；否则删除或改为普通描述。",
        replacement="相关信息以官方说明为准",
        category_scope=["general"],
    ),
    AdLawIssueDefinition(
        issue_type="education_commitment",
        label="教育培训保证性承诺",
        risk_level="high",
        terms=["通过率100%", "百分百高薪就业", "国家承认", "保过", "包过", "成绩飞跃", "过目不忘"],
        reason="教育、培训广告不得对考试、升学、证书或培训效果作保证性承诺。",
        suggestion="改为课程体系、师资、服务内容等可证明信息。",
        replacement="提供系统课程与学习支持",
        category_scope=["education"],
    ),
    AdLawIssueDefinition(
        issue_type="finance_return_commitment",
        label="金融理财收益承诺",
        risk_level="high",
        terms=["保本保息", "稳赚不赔", "高收益", "年化收益率", "内幕消息", "原始股", "躺着赚钱"],
        reason="金融理财或投资相关广告不得承诺保本、无风险或确定收益。",
        suggestion="增加风险提示，删除保证收益和无风险表达。",
        replacement="投资有风险，信息仅供参考",
        category_scope=["finance"],
    ),
    AdLawIssueDefinition(
        issue_type="real_estate_mislead",
        label="房地产误导宣传",
        risk_level="high",
        terms=["升值", "保值", "投资回报", "学区房", "最佳地段", "绝版地段", "风水宝地"],
        reason="房地产广告不得承诺升值或投资回报，也不得对配套、位置等作误导宣传。",
        suggestion="改为真实、清楚的房源和配套信息。",
        replacement="配套信息以官方公示为准",
        category_scope=["real_estate"],
    ),
]


class AdLawRetriever:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or LEGAL_RESOURCE_ROOT

    def retrieve(self, content: str, category: str = "general", limit: int = 8) -> AdLawRetrievalResult:
        normalized_category = CATEGORY_ALIASES.get(category, category or "general")
        load_result = _cached_load(str(self.root_dir))
        if load_result.warnings:
            raise KnowledgeSourceError("；".join(load_result.warnings))
        hits = self._find_issue_hits(content, normalized_category)
        segments = (
            self._rank_segments(content, normalized_category, hits, load_result.segments)[:limit]
            if hits
            else []
        )
        return AdLawRetrievalResult(issue_hits=hits, evidence_segments=segments, warnings=load_result.warnings)

    def _find_issue_hits(self, content: str, category: str) -> List[AdLawIssueHit]:
        hits: List[AdLawIssueHit] = []
        seen = set()
        for definition in ISSUE_DEFINITIONS:
            if category not in definition.category_scope and "general" not in definition.category_scope:
                continue
            for term in sorted(definition.terms, key=len, reverse=True):
                for match in re.finditer(re.escape(term), content, flags=re.IGNORECASE):
                    key = (definition.issue_type, match.start(), match.end())
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(
                        AdLawIssueHit(
                            issue_type=definition.issue_type,
                            label=definition.label,
                            risk_level=definition.risk_level,
                            span=content[match.start() : match.end()],
                            start=match.start(),
                            end=match.end(),
                            reason=definition.reason,
                            suggestion=definition.suggestion,
                            replacement=definition.replacement,
                            source={
                                "detector": "ad_law_document_retriever",
                                "rule_id": definition.issue_type,
                                "version": "ad-law-doc-v1",
                                "source_type": "document_index",
                                "priority": 10 if definition.risk_level == "high" else 30,
                            },
                        )
                    )
        return sorted(hits, key=lambda item: (item.start, item.issue_type))

    def _rank_segments(
        self,
        content: str,
        category: str,
        hits: List[AdLawIssueHit],
        segments: List[KnowledgeSegment],
    ) -> List[KnowledgeSegment]:
        issue_terms = list({hit.label for hit in hits} | {hit.issue_type for hit in hits} | {hit.span for hit in hits})
        query = RetrievalQuery(
            content=content,
            category=category,
            scope="ad_law",
            issue_terms=issue_terms,
            boost_terms=["广告", "虚假", "误导", "绝对化", "证明", "功效", "承诺"],
        )
        return rank_knowledge_segments(
            segments,
            query,
            source_type_boosts={"ad_law_pdf": 1, "prohibited_terms_docx": 2},
            category_boost=8,
            issue_term_boost=6,
        )


@lru_cache(maxsize=4)
def _cached_load(root_dir: str):
    return AdLawDocumentLoader(Path(root_dir)).load()
