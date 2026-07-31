from __future__ import annotations

import json
from typing import Any, Dict

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.modeling.messages import ModelMessage

from .release_contracts import REVIEW_RISK_FAMILIES, normalize_text


def draft_messages(
    task: ReleaseTask,
    *,
    mode: str,
    instruction: str,
    excluded_requirements: list[str] | None = None,
) -> list[ModelMessage]:
    review_constraints = _active_review_constraints(task)
    targeted_ids = {
        item["requirement_id"]
        for item in review_constraints
        if item["requirement_id"]
    }
    payload = {
        "product": {"name": task.product_name, "category": task.product_category},
        "platform": task.platform,
        "objective": task.objective,
        "requirements": [
            item.to_context_dict()
            for item in task.active_requirements
            if item.requirement_id not in targeted_ids
        ],
        "current_draft": task.current_draft if mode == "revision" else "",
        "mode": mode,
        "instruction": instruction,
        "excluded_requirements": list(excluded_requirements or []),
        "review_constraints": review_constraints,
        "product_image_analysis": task.image_analysis,
    }
    return [
        ModelMessage(
            role="system",
            content=(
                "你是电商宣传文案起草工具，不负责合规审核。根据输入事实和要求生成一版可直接展示的文案。"
                "保留所有用户要求，包括可能有风险的要求，由后续 Review 独立判断；不得擅自删改品牌、型号、参数、价格或卖点。"
                "review_constraints 是已经由 Review 明确要求处置的例外：非空时必须按 reason 删除或安全改写 target_requirement 的风险语义，"
                "不能只换同义词规避 matched_text；其中不带风险的价格、规格等事实可以保留，其他 requirements 必须保留。"
                "excluded_requirements 是用户本轮明确删除或替换的旧要求，不得出现在正文、标题或标签中。"
                "可以增加不改变事实含义的场景组织、主观润色和平台内行动号召，但不得新增使用经历或可核验事实。"
                "product_image_analysis.visible_text 是包装上可直接观察到的原文，可以作为文案事实来源；"
                "visible_features 只证明外观，不得据此推导材质性能、适用人群、耐用程度、使用结果或保证。"
                "纯情绪和审美表达可以创作，但不能把规格或可见外观扩写成新的产品属性。"
                "不得改变用户陈述的来源、适用范围和承诺强度，也不得把主观表达包装成客观背书、排名或保证。"
                "不得虚构链接、联系方式、账号或站外入口；普通行动号召只能指向当前平台内的正常购买行为。"
                "用户只提供普通价格时，不能自行添加活动、限时、限量、库存、渠道、优惠资格或稀缺性条件；"
                "行动号召也不能暗示未提供的促销正在发生。"
                "输出前核对数字、比例、时长、价格和购买路径，不得从已有信息推导或补充新的客观事实。"
                "用户没有提出时，不得自行新增最高级、排名、全称人群或无条件承诺。"
                "revision 模式应按 instruction 修改 current_draft；review_constraints 非空时，按其中明确决策完成整稿重写。"
                "primary_draft 只包含面向消费者的文案，不包含流程、审核或内部说明。"
                "只返回 JSON：{\"primary_draft\":\"...\"}。"
            ),
        ),
        ModelMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    ]


def review_messages(
    task: ReleaseTask,
    content: str,
    law_context: Dict[str, Any],
    platform_context: Dict[str, Any],
    *,
    excluded_requirement_ids: set[str] | None = None,
) -> list[ModelMessage]:
    payload = {
        "content": content,
        "product": {"name": task.product_name, "category": task.product_category},
        "platform": task.platform,
        "requirements": [
            _review_requirement_context(item)
            for item in task.active_requirements
            if item.requirement_id not in (excluded_requirement_ids or set())
        ],
        "confirmed_evidence": _active_confirmed_evidence(task),
        "product_image_analysis": task.image_analysis,
        "ad_law_evidence": compact_knowledge(law_context),
        "platform_evidence": compact_knowledge(platform_context),
    }
    schema = {
        "rewrite_mode": "none|targeted|full",
        "needs_more_context": False,
        "question": "",
        "decisions": [
            {
                "requirement_id": "req-id 或空字符串",
                "matched_text": "从 content 原样复制的最小风险片段",
                "label": "简短风险名",
                "risk_family": "枚举值",
                "severity": "low|medium|high|critical",
                "action": "advisory|rewrite|block|confirm",
                "reason": "一句话原因",
            }
        ],
        "missing_requirement_ids": ["完全未在 content 中落实的 req-id"],
    }
    return [
        ModelMessage(
            role="system",
            content=(
                "你是本任务唯一的语义合规审核者。检索材料只是证据线索，你必须结合完整语义独立判断，代码不会替你改变风险动作。\n"
                "审核当前 content 和仍有效的 requirements。requirements 即使未写入正文也必须判断其语义风险。\n"
                "动作定义：advisory=可发布的非阻断建议；rewrite=明确风险且可改写；block=明确不能原样发布；"
                "confirm=合规性确实取决于用户持有依据，需要人工确认。"
                "confirm 只用于资质或授权、代言许可、受监管功效、可执行的保证赔付，以及带时间、库存、资格、满减或折扣条件的重大促销；"
                "必须关联 requirement_id。用户提供的商品身份、品牌型号、规格成分、普通价格、数量总价、物理或功能参数不因外部不可核验而确认。\n"
                "判断每项表达时依次确认事实来源、声明类型、适用范围、承诺强度和作用对象。"
                "普通主观评价、有边界的使用体验、适用场景和非保证式推荐属于正常宣传，默认可发布，最多 advisory；"
                "不能仅因外部无法核验、条件没有穷举或语气较肯定就升级处理。"
                "product_image_analysis.visible_text 是包装上可直接观察到的原文，可作为已有事实；"
                "visible_features 只证明外观，不能为材质性能、适用人群、耐用程度、使用结果或保证提供依据。"
                "逐项区分用户要求、包装可见原文、纯主观创意和 Draft 新增的可核验事实；"
                "不得把规格推导出的新属性当成已提供事实，也不要把纯情绪或审美修辞误判为事实风险。"
                "只有语义明确形成客观排名、竞品优劣、无条件结果保证、赔付责任，或指向疾病、生理指标和受监管功效时，才按对应风险处理。"
                "unprovided_material_claim 只用于 Draft 自行新增的重大事实，不能用于已有 requirement。"
                "用户提供的普通价格若被 Draft 自行加上活动、限时、限量、库存、渠道或优惠资格条件，"
                "应只匹配新增条件，按 draft_generated 的 unprovided_material_claim 直接 rewrite；"
                "品牌名按整体标识理解，不能拆词制造风险。文案自行新增且用户未提供的重大可核验事实应 rewrite。\n"
                "独立核对 content 中的数字、比例、价格、时长和型号：若某个数字不在 product 或 requirements 中，"
                "即使可由已有数字计算或推导，也属于 Draft 新增事实，必须以 unprovided_material_claim 要求 rewrite。\n"
                "当前平台内的正常选购和购买引导可发布；只有引向外部平台、联系方式、私下交易或绕过当前平台时才按导流处置。"
                "互动风险只针对诱导刷量或虚假互动，不适用于普通购买行动。\n"
                "每个 decision 的 matched_text 必须从 content 原样复制最小风险片段；"
                "若风险 requirement 被 Draft 漏掉，则填写 requirement_id 并原样复制该要求文本。"
                "不得引用表情、无关文字或已不在 requirements 中的历史内容。能关联 requirement 时填写其 ID，"
                "文案对用户要求做了语义等价改写时仍必须关联该 requirement_id，不能仅因措辞变化标成 draft-generated。"
                "文案自行新增的问题留空。缺失的风险要求按其语义返回 decision，不能仅因缺失改成 confirm；"
                "missing_requirement_ids 只列完全未落实且没有风险 decision 的普通要求，已原样或语义等价表达的要求不要列入。"
                "要求缺失、要求冲突、删除指令或风格偏好未落实，只能列入 missing_requirement_ids，不能伪装成风险 decision。"
                "否定或排除要求只约束用户明确点名的对象；不得擅自扩大到翻译、同义表达、品牌名、型号名或关联概念。"
                "点名对象没有出现在 content 时，该否定要求已落实，不要要求正文复述这条指令。\n"
                "有 rewrite/block 时 rewrite_mode 必须为 targeted 或 full；仅当风险贯穿全文、结构失效或多项要求冲突时用 full。"
                "没有 rewrite/block 时必须为 none。只有确实缺少判断所需信息且不能用 confirm 处理时才 needs_more_context=true，"
                "并填写 question；不要用它询问普通商品事实。已在 confirmed_evidence 中匹配确认的事实不要再次 confirm。\n"
                "risk_family 只能取：" + ",".join(REVIEW_RISK_FAMILIES) + "。\n"
                "输出前检查明确排名、具体竞品优劣、站外导流、私下交易和依据型声明是否漏报；"
                "content 和当前 requirements 都未出现的表达绝不能依据历史内容报风险。\n"
                "只返回符合此结构的 JSON：" + json.dumps(schema, ensure_ascii=False)
            ),
        ),
        ModelMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    ]


def rewrite_messages(task: ReleaseTask, review: Dict[str, Any]) -> list[ModelMessage]:
    decisions = [
        item
        for item in review.get("decisions", [])
        if isinstance(item, dict) and item.get("action") in {"rewrite", "block"}
    ]
    targeted_ids = {
        str(item.get("requirement_id", "")) for item in decisions
        if str(item.get("requirement_id", ""))
    }
    requirements = {item.requirement_id: item for item in task.active_requirements}
    payload = {
        "current_draft": task.current_draft,
        "product": {"name": task.product_name, "category": task.product_category},
        "platform": task.platform,
        "requirements": [
            item.to_context_dict()
            for item in task.active_requirements
            if item.requirement_id not in targeted_ids
        ],
        "decisions": [
            {
                "requirement_id": str(item.get("requirement_id", "")),
                "target_requirement": (
                    requirements[str(item.get("requirement_id", ""))].source_text
                    if str(item.get("requirement_id", "")) in requirements
                    else ""
                ),
                "matched_text": str(item.get("matched_text", "")),
                "action": str(item.get("action", "")),
                "confirmation_resolution": str(
                    item.get("confirmation_resolution", "")
                ),
                "reason": str(item.get("reason", "")),
            }
            for item in decisions
        ],
    }
    return [
        ModelMessage(
            role="system",
            content=(
                "你是风险改写工具，只执行 Review 给出的 decisions。删除或安全改写指定片段，其他用户事实和要求保持不变。"
                "decisions 中的 target_requirement 不属于必须原样保留项；必须根据 reason 去除其风险语义，不能只替换 matched_text 的字面措辞。"
                "confirmation_resolution=rewrite_without_basis 表示用户选择去除待确认风险：只删除依赖依据的条件、背书或保证语义，"
                "同一要求中可独立成立的普通价格、数量、规格和产品事实必须保留并自然改写。"
                "目标中不带风险的普通价格、规格或产品事实应尽量保留。"
                "品牌名是整体标识，不得拆词、弱化、重命名或删除。不得新增使用经历、参数、资质、排名、品质结论、"
                "市场表现或其他未提供事实；没有事实支持的安全替换应直接删除。"
                "不要用认证、口碑、公认、首选、放心保证等新结论填补被删除的风险片段。"
                "只返回 JSON：{\"primary_draft\":\"...\"}。"
            ),
        ),
        ModelMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    ]


def _review_requirement_context(item: Any) -> Dict[str, Any]:
    context = item.to_context_dict()
    if item.source_text and item.source_text != item.text:
        context["source_text"] = item.source_text
    return context


def _active_review_constraints(task: ReleaseTask) -> list[Dict[str, str]]:
    constraints = []
    seen = set()
    requirements = {item.requirement_id: item for item in task.active_requirements}
    for revision in task.revisions:
        if revision.status != "candidate":
            continue
        for item in revision.review.get("decisions", []):
            if not isinstance(item, dict) or item.get("action") not in {"rewrite", "block"}:
                continue
            key = (
                str(item.get("requirement_id", "")),
                normalize_text(item.get("matched_text", "")),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            constraints.append(
                {
                    "requirement_id": key[0],
                    "target_requirement": (
                        requirements[key[0]].source_text if key[0] in requirements else ""
                    ),
                    "matched_text": str(item.get("matched_text", "")),
                    "reason": str(item.get("reason", "")),
                }
            )
    return constraints


def _active_confirmed_evidence(task: ReleaseTask) -> list[Dict[str, Any]]:
    requirements = {item.requirement_id: item for item in task.active_requirements}
    active = []
    for evidence in task.confirmed_evidence:
        requirement = requirements.get(str(evidence.get("requirement_id", "")))
        if requirement is None:
            continue
        if normalize_text(evidence.get("requirement_source_text", "")) != normalize_text(
            requirement.source_text
        ):
            continue
        active.append(dict(evidence))
    return active


def compact_knowledge(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "issue_hits": [
            {
                "issue_type": str(item.get("issue_type", "")),
                "label": str(item.get("label", "")),
                "span": str(item.get("span", "")),
                "risk_level": str(item.get("risk_level", "")),
                "reason": str(item.get("reason", "")),
                "source": item.get("source", {}),
            }
            for item in value.get("issue_hits", [])[:6]
            if isinstance(item, dict)
        ],
        "evidence_segments": [
            {
                "segment_id": str(item.get("segment_id", "")),
                "title": str(item.get("title", "")),
                "content": str(item.get("content", ""))[:320],
            }
            for item in value.get("evidence_segments", [])[:4]
            if isinstance(item, dict)
        ],
    }
