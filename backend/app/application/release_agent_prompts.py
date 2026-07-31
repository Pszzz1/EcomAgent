from __future__ import annotations

import json

from backend.app.domain import ReleaseTask
from backend.app.infrastructure.modeling.messages import ModelMessage


def initial_task_messages(task: ReleaseTask, user_message: str) -> list[ModelMessage]:
    existing = {
        "product": {"name": task.product_name, "category": task.product_category},
        "platform": task.platform,
        "objective": task.objective,
        "requirements": [item.to_context_dict() for item in task.active_requirements],
        "has_user_draft": bool(task.current_draft),
    }
    return [
        ModelMessage(
            role="system",
            content=(
                "你是电商营销发布任务的初始理解器，只把首次用户输入转换为任务结构。"
                "不要生成文案，不要审核，不要弱化、改写或美化用户要求。"
                "每项 requirement 的 source_text 必须逐字复制用户输入中的连续原文片段；"
                "不同事实、卖点、风格和内容要求应拆成独立项，不得遗漏风险表达。"
                "商品名、品牌、品类、平台和发布目标写入 task_updates；宣传参数、卖点、风格和内容要求写入 requirements。"
                "只要首次输入明确提供了平台、商品名或品类，就必须提取到 task_updates，不能因为无法生成完整文案而留空。"
                "如果确实没有提供某项信息，才允许该字段为空；不得把可识别的用户输入描述为乱码或不可识别。"
                "风险要求也必须忠实保留原文，后续 Review 会独立判断，初始理解器无权提前分类或改写。"
                "kind 表示语义类型：商品参数是 fact，商品优势、主观卖点或适用人群是 selling_point，"
                "对文案语气或画面风格的指令才是 style；不要把商品卖点误判为 style。"
            ),
        ),
        ModelMessage(
            role="user",
            content=(
                "已有结构化输入：\n"
                + json.dumps(existing, ensure_ascii=False)
                + "\n\n首次用户输入：\n"
                + user_message.strip()
                + "\n\n返回 JSON：{summary,task_updates,requirements,instruction}。"
                "task_updates 仅包含 {product_name,product_category,platform,objective}，"
                "platform 只能是空字符串、douyin、kuaishou 或 xiaohongshu。"
                "requirements 每项严格为 {source_text,kind}；"
                "kind 只能是 fact、selling_point、style 或 content。"
            ),
        ),
    ]


def turn_decision_messages(
    task: ReleaseTask,
    user_message: str,
    *,
    available_actions: list[str],
    current_phase: str = "",
) -> list[ModelMessage]:
    state = {
        "current_phase": current_phase,
        "available_actions": available_actions,
        "product": {"name": task.product_name, "category": task.product_category},
        "platform": task.platform,
        "objective": task.objective,
        "requirements": [item.to_context_dict() for item in task.active_requirements],
        "requirements_removed_by_risk_optimization": [
            item.to_context_dict()
            for item in task.requirements
            if item.status == "removed_for_compliance"
        ],
        "current_revision": task.current_revision,
        "restorable_revisions": [item.revision for item in task.restorable_revisions],
        "current_draft": task.current_draft,
        "current_review": {
            "conclusion": task.current_review.get("publication_conclusion", ""),
            "decisions": task.current_review.get("decisions", []),
        },
        "pending_confirmation": task.pending_confirmation,
        "promotion_image": {
            "status": task.promotion_image.get("status", ""),
            "display_text": task.promotion_image.get("display_text", []),
        },
        "recent_conversation": task.conversation[:-1][-4:],
    }
    return [
        ModelMessage(
            role="system",
            content=(
                "你是电商营销内容发布任务的 Agent Controller。每轮只决定当前用户真正想做的事，"
                "并把用户新增、修改、删除的要求原子化。不要生成文案，不要执行审核。\n"
                "intent 必须从当前状态给出的 available_actions 中选择，不得选择列表外的动作。"
                "动作含义：draft=首次生成，revise=修改当前稿，review=审核用户已有稿，"
                "explain=回答为什么或风险是什么，compare=比较版本，restore=恢复历史文案，"
                "confirm=处理等待确认的宣传事实，generate_image=为已确认文案生成宣传图，"
                "revise_image=根据反馈修改当前宣传图，clarify=缺少继续执行所必需的信息。\n"
                "用户明确提供的普通商品事实和宣传偏好可以直接记录；只有继续执行确实缺少信息时才追问。"
                "解释、比较、回退、确认不得创建 requirement mutation。用户询问‘怎么了’时结合最近审核与对话回答，"
                "不能机械地当成改写要求。只有继续完成任务真正不可缺少的信息才能追问。\n"
                "存在 pending_confirmation 时，用户用自然语言逐项决定后，intent=confirm，confirmation_resolutions 必须恰好覆盖全部待确认 decision_id；"
                "每项 resolution 只能是 confirmed_with_basis 或 rewrite_without_basis。没有 pending_confirmation 时禁止使用 confirm；"
                "用户只是询问原因时仍应 explain。\n"
                "requirements_removed_by_risk_optimization 是此前按用户选择从文案中移除的原始要求。"
                "用户后来明确确认其中某项真实并要求重新保留时，将其 requirement_id 放入 reactivate_requirement_ids；"
                "该字段与主意图正交且只能配合 restore 或 revise：同时要求恢复历史文案仍用 restore，同时要求修改当前稿仍用 revise。"
                "确认被移除要求后，用户要求恢复或恢复原文案、且没有提出新的改写方式时，用 restore 且 target_revision=0；"
                "用户要求把该事实加入当前稿或同时提出新的表达方式时，才用 revise。"
                "不得因为用户只说恢复文案就自动重新启用要求。\n"
                "现有 requirements 默认继续有效，不要逐项回传保留决定。"
                "只有用户明确纯删除的旧要求才把 requirement_id 放入 remove_requirement_ids；"
                "修改旧要求时必须在 new_requirements 项内填写 replaces_requirement_id，"
                "把被替换旧项和修改后要求绑定为一个原子变更，不要再把该 ID 放入 remove_requirement_ids；"
                "删除和替换同时出现时分别处理，不能用删除无关要求代替被修改的旧要求。"
                "每条新增事实、卖点、风格或内容要求都进入 new_requirements；source_text 必须逐字复制本轮用户输入中的连续原文片段，"
                "该原文片段就是任务要求，Controller 无权先行弱化、改写或重新解释；"
                "例如用户输入‘价格改成89元’，source_text 必须是‘价格改成89元’，不能改写成‘售价89元’；"
                "同时 replaces_requirement_id 必须指向当前旧价格要求。新增而非替换时 replaces_requirement_id 为空字符串；"
                "本轮包含多个独立要求时必须拆成多项，不得把卖点、事实和风格合并。"
                "解释、比较、回退和确认等意图的 remove_requirement_ids 与 new_requirements 必须为空。"
                "restore 时，用户明确指定 v2 等版本则 target_revision=2；只说恢复上一版时 target_revision=0。"
                "生成或修改宣传图不修改文案要求，remove_requirement_ids 与 new_requirements 也必须为空。"
                "当 current_phase=promotion_image_review_ready 时，本轮输入默认是在评价或修改当前宣传图；"
                "涉及画面标识、构图、颜色、视觉风格、文字位置等反馈应使用 revise_image，"
                "只有用户明确说要修改文案、标题、正文或商品事实时才使用 revise。"
                "promotion_image.display_text 是生图前规划的文字，不代表对生成图片像素的识别结果；"
                "用户询问图片中未出现在当前状态里的文字或视觉细节时，不得编造含义，应如实说明当前无法核验，"
                "并根据用户意愿解释现有规划或使用 revise_image 重新生成。"
                "Controller 不判断广告风险，也不把风险表达改成普通促销；只忠实维护用户实际要求。"
                "task_updates 只允许 product_name、product_category、platform、objective。"
                "platform 必须返回 douyin、kuaishou 或 xiaohongshu，不得返回中文平台名。"
            ),
        ),
        ModelMessage(
            role="user",
            content=(
                "当前任务状态：\n"
                + json.dumps(state, ensure_ascii=False)
                + "\n\n本轮用户输入：\n"
                + user_message.strip()
                + "\n\n返回 JSON："
                "{intent,summary,task_updates,remove_requirement_ids,new_requirements,reactivate_requirement_ids,confirmation_resolutions,answer,question,target_revision}。"
                "remove_requirement_ids 只能包含本轮明确删除或替换的现有 requirement_id；"
                "new_requirements 每项严格为 "
                "{source_text,kind:fact|selling_point|style|content,replaces_requirement_id}；"
                "不得使用 update、operation、id、text、type、content 等其他字段。"
                "没有逐项人工确认动作时 confirmation_resolutions 必须为空数组。"
            ),
        ),
    ]
