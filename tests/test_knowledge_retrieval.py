import pytest

from backend.app.knowledge import AdLawRetriever, PlatformPolicyRetriever
from backend.app.knowledge.models import KnowledgeSegment, KnowledgeSourceError, RetrievalQuery
from backend.app.knowledge.search import rank_knowledge_segments


def test_common_ranker_attaches_retrieval_metadata() -> None:
    segment = KnowledgeSegment(
        segment_id="s1",
        source_file="rules.docx",
        source_type="platform_policy_docx",
        title="平台规则",
        content="平台不得引导用户添加微信或进行站外交易。",
        categories=["douyin", "platform_policy", "general"],
        scope="platform_policy",
        platform="douyin",
    )

    ranked = rank_knowledge_segments(
        [segment],
        RetrievalQuery(
            content="加微信领取福利",
            platform="douyin",
            scope="platform_policy",
            issue_terms=["微信"],
            boost_terms=["站外交易"],
        ),
    )

    assert ranked
    evidence = ranked[0].to_dict()
    assert evidence["scope"] == "platform_policy"
    assert evidence["platform"] == "douyin"
    assert evidence["metadata"]["retrieval_score"] > 0
    assert any(reason.startswith("issue_term:") for reason in evidence["metadata"]["retrieval_reasons"])


def test_ad_law_retriever_returns_unified_evidence_segments() -> None:
    result = AdLawRetriever().retrieve("全网最低价，100%有效。", category="beauty")

    assert result.issue_hits
    assert result.evidence_segments
    evidence = result.evidence_segments[0].to_dict()
    assert evidence["scope"] == "ad_law"
    assert evidence["source_file"] in {"广告法.pdf", "广告法禁用词.docx"}
    assert evidence["metadata"]["retrieval_score"] > 0
    assert evidence["metadata"]["retrieval_reasons"]


def test_platform_retriever_returns_platform_scoped_evidence_segments() -> None:
    result = PlatformPolicyRetriever().retrieve("加微信领取专属优惠，去抖音看详情。", platform="kuaishou")

    assert result.issue_hits
    assert result.evidence_segments
    evidence = result.evidence_segments[0].to_dict()
    assert evidence["scope"] == "platform_policy"
    assert evidence["platform"] == "kuaishou"
    assert evidence["source_file"] == "快手平台规则.docx"
    assert evidence["metadata"]["retrieval_score"] > 0
    assert any("platform:kuaishou" == reason for reason in evidence["metadata"]["retrieval_reasons"])


def test_retrievers_do_not_inject_unrelated_evidence_without_a_hit() -> None:
    ad_law = AdLawRetriever().retrieve("普通商品日常使用方便。")
    platform = PlatformPolicyRetriever().retrieve(
        "普通商品日常使用方便。",
        platform="xiaohongshu",
    )

    assert ad_law.issue_hits == []
    assert ad_law.evidence_segments == []
    assert platform.issue_hits == []
    assert platform.evidence_segments == []


def test_emotional_metaphor_does_not_preclassify_as_a_medical_claim() -> None:
    result = AdLawRetriever().retrieve("这份甜点带来治愈心情。", category="food")

    assert not any(hit.issue_type == "medicalized_claim" for hit in result.issue_hits)


def test_cross_platform_retrieval_requires_a_direct_diversion_relation() -> None:
    retriever = PlatformPolicyRetriever()

    ordinary_scene = retriever.retrieve("日常刷微博、看小红书，回复消息也很顺手。", platform="douyin")
    diversion = retriever.retrieve("更多参数去微博看详情。", platform="douyin")

    assert not any(
        hit.issue_type == "third_party_platform_diversion"
        for hit in ordinary_scene.issue_hits
    )
    assert any(
        hit.issue_type == "third_party_platform_diversion" and hit.span == "微博"
        for hit in diversion.issue_hits
    )


def test_ad_law_retriever_fails_when_required_documents_are_missing(tmp_path) -> None:
    with pytest.raises(KnowledgeSourceError, match="广告法禁用词"):
        AdLawRetriever(root_dir=tmp_path).retrieve("普通商品文案")


def test_platform_retriever_fails_when_selected_platform_document_is_missing(tmp_path) -> None:
    with pytest.raises(KnowledgeSourceError, match="抖音平台规则"):
        PlatformPolicyRetriever(root_dir=tmp_path).retrieve("普通商品文案", platform="douyin")
