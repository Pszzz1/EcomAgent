from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.application import ReleaseAgentController, ReleasePackageBuilder
from backend.app.domain import ReleaseTask
from backend.app.infrastructure.execution import ModelExecutionOutcome
from backend.app.tools import (
    CandidateReviewTool,
    DraftCopyTool,
    ProductImageAnalysisTool,
    PromotionImageGenerationTool,
    PromotionImagePromptTool,
    RiskRewriteTool,
    ToolOutcome,
    resolve_review_confirmations,
)


MAX_TRACE_EVENTS = 200


class ReleaseGraphState(TypedDict, total=False):
    task_id: str
    task: Dict[str, Any]
    base_task: Dict[str, Any]
    input_payload: Dict[str, Any]
    user_message: str
    confirmation_resolutions: List[Dict[str, Any]]
    is_new_task: bool
    turn_id: str
    turn_fingerprint: str
    turn_kind: str
    active_turn_id: str
    active_turn_fingerprint: str
    last_completed_turn_id: str
    last_completed_turn_fingerprint: str
    decision: Dict[str, Any]
    status: str
    phase: str
    answer: str
    next_questions: List[str]
    traces: List[Dict[str, Any]]
    draft_mode: str
    full_redraft_attempted: bool
    requirement_repair_attempted: bool
    review_candidate: Dict[str, Any]
    promotion_image_plan: Dict[str, Any]
    promotion_image_instruction: str
    business_phase: str
    route: str


class ReleaseAgentGraph:
    """LangGraph lifecycle for one durable, multi-turn release task."""

    def __init__(
        self,
        *,
        agent_controller: ReleaseAgentController,
        draft_tool: DraftCopyTool,
        review_tool: CandidateReviewTool,
        risk_rewrite_tool: RiskRewriteTool,
        image_analysis_tool: ProductImageAnalysisTool,
        promotion_image_prompt_tool: PromotionImagePromptTool,
        promotion_image_generation_tool: PromotionImageGenerationTool,
        package_builder: ReleasePackageBuilder,
        checkpointer: Any,
    ) -> None:
        self.agent_controller = agent_controller
        self.draft_tool = draft_tool
        self.review_tool = review_tool
        self.risk_rewrite_tool = risk_rewrite_tool
        self.image_analysis_tool = image_analysis_tool
        self.promotion_image_prompt_tool = promotion_image_prompt_tool
        self.promotion_image_generation_tool = promotion_image_generation_tool
        self.package_builder = package_builder
        self.compiled = self._build().compile(checkpointer=checkpointer)

    def invoke(self, state: Dict[str, Any], *, task_id: str) -> ReleaseGraphState:
        return self.compiled.invoke(
            state,
            config={"configurable": {"thread_id": task_id}},
            durability="sync",
        )

    def resume(self, task_id: str) -> ReleaseGraphState:
        return self.compiled.invoke(
            None,
            config={"configurable": {"thread_id": task_id}},
            durability="sync",
        )

    def snapshot(self, task_id: str) -> ReleaseGraphState:
        snapshot = self.compiled.get_state({"configurable": {"thread_id": task_id}})
        return dict(snapshot.values or {})

    def _build(self) -> StateGraph:
        graph = StateGraph(ReleaseGraphState)
        graph.add_node("prepare_turn", self._prepare_turn)
        graph.add_node("analyze_product_image", self._analyze_product_image)
        graph.add_node("decide_action", self._decide_action)
        graph.add_node("apply_decision", self._apply_decision)
        graph.add_node("resolve_confirmation", self._resolve_confirmation)
        graph.add_node("draft_copy", self._draft_copy)
        graph.add_node("candidate_review", self._candidate_review)
        graph.add_node("apply_review_result", self._apply_review_result)
        graph.add_node("risk_rewrite", self._risk_rewrite)
        graph.add_node("plan_promotion_image", self._plan_promotion_image)
        graph.add_node("generate_promotion_image", self._generate_promotion_image)
        graph.add_node("build_release_package", self._build_release_package)
        graph.add_edge(START, "prepare_turn")
        for node, routes in {
            "prepare_turn": [
                "analyze_product_image",
                "decide_action",
                "resolve_confirmation",
                "draft_copy",
                "plan_promotion_image",
                "build_release_package",
                "end",
            ],
            "analyze_product_image": ["decide_action", "end"],
            "decide_action": ["apply_decision", "end"],
            "apply_decision": [
                "draft_copy",
                "candidate_review",
                "risk_rewrite",
                "resolve_confirmation",
                "plan_promotion_image",
                "end",
            ],
            "resolve_confirmation": ["decide_action", "risk_rewrite", "end"],
            "draft_copy": ["candidate_review", "end"],
            "candidate_review": ["draft_copy", "apply_review_result", "end"],
            "apply_review_result": [
                "draft_copy",
                "risk_rewrite",
                "end",
            ],
            "risk_rewrite": ["candidate_review", "draft_copy", "end"],
            "plan_promotion_image": ["generate_promotion_image", "end"],
            "generate_promotion_image": ["end"],
            "build_release_package": ["end"],
        }.items():
            graph.add_conditional_edges(
                node,
                _route,
                {route: END if route == "end" else route for route in routes},
            )
        return graph

    def _prepare_turn(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        business_phase = self._business_phase(task)
        turn_id = str(state.get("turn_id", ""))
        if turn_id and turn_id == str(state.get("last_completed_turn_id", "")):
            state["route"] = "end"
            return state

        message = str(state.get("user_message", "")).strip()
        confirmation_resolutions = list(state.get("confirmation_resolutions", []))
        turn_kind = str(state.get("turn_kind", "conversation"))
        is_new_task = not task.conversation and task.state_version == 0
        state.update(
            {
                "base_task": task.to_snapshot(),
                "business_phase": business_phase,
                "decision": {},
                "status": "running",
                "phase": "running",
                "answer": "",
                "next_questions": [],
                "draft_mode": "",
                "full_redraft_attempted": False,
                "requirement_repair_attempted": False,
                "promotion_image_plan": {},
                "promotion_image_instruction": "",
                "is_new_task": is_new_task,
                "active_turn_id": turn_id,
                "active_turn_fingerprint": str(state.get("turn_fingerprint", "")),
            }
        )
        if turn_kind == "replace_image":
            task.append_conversation("user", "更换商品实物图")
        elif message:
            task.append_conversation("user", message)
        elif confirmation_resolutions:
            task.append_conversation(
                "user",
                f"提交 {len(confirmation_resolutions)} 项宣传事实处理决定",
            )
        state["task"] = task.to_snapshot()

        if task.source_image_asset_id and not task.image_analysis:
            state["route"] = "analyze_product_image"
        elif confirmation_resolutions:
            state["route"] = "resolve_confirmation"
        elif message:
            state["route"] = "decide_action"
        elif self._copy_ready(task):
            image_status = str(task.promotion_image.get("status", ""))
            if image_status == "awaiting_user":
                task.accept_promotion_image()
                state["task"] = task.to_snapshot()
                state["route"] = "build_release_package"
            elif image_status == "accepted":
                state["route"] = "build_release_package"
            else:
                state["route"] = "plan_promotion_image"
        elif task.pending_confirmation:
            self._finish(
                state,
                task,
                status="waiting_user",
                phase="evidence_confirmation",
                answer="当前仍有宣传事实等待处理，请确认具有真实依据，或接受风险改写。",
                questions=_confirmation_questions(task),
            )
        else:
            self._finish(
                state,
                task,
                status="waiting_user",
                phase="draft_revision_needed",
                answer="当前工作稿尚未达到可交付状态，请继续说明修改要求。",
            )
        return state

    def _analyze_product_image(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        outcome = self.image_analysis_tool.run(task)
        self._record_execution(state, outcome, tool_name=outcome.tool_name)
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"商品实物图分析未完成：{outcome.error}",
                outcome.error_type,
            )
        task.image_analysis = dict(outcome.parsed)
        state["task"] = task.to_snapshot()
        if str(state.get("turn_kind", "")) == "replace_image":
            if task.image_analysis.get("quality_level") == "retake_required":
                issues = "、".join(
                    str(item) for item in task.image_analysis.get("quality_issues", [])
                )
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase="source_image_retake_required",
                    answer=(
                        "新图片仍不足以可靠识别商品事实。"
                        + (f"发现：{issues}。" if issues else "")
                        + "请继续更换更清晰、完整的实物图。"
                    ),
                )
            else:
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase=self._business_phase(task),
                    answer="新实物图已完成分析，当前文案保持不变；后续生成或修改会使用新的图片事实。",
                )
            return state
        state["route"] = "decide_action"
        return state

    def _decide_action(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        user_message = str(state.get("user_message", ""))
        is_new_task = bool(state.get("is_new_task", False))
        if is_new_task:
            node_name = "understand_initial_task"
            outcome = self.agent_controller.understand_initial_task(task, user_message)
        else:
            node_name = "decide_turn"
            outcome = self.agent_controller.decide_turn(
                task,
                user_message,
                current_phase=str(state.get("business_phase", "")),
            )
        self._record_execution(
            state,
            outcome,
            tool_name=node_name,
        )
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"本轮意图理解未完成：{outcome.error}",
                outcome.error_type,
            )
        state["is_new_task"] = False
        state["decision"] = dict(outcome.parsed)
        state["route"] = "apply_decision"
        return state

    def _apply_decision(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        decision = dict(state.get("decision", {}))
        intent = str(decision.get("intent", ""))
        restored = None
        if intent == "restore":
            target_revision = int(decision.get("target_revision", 0) or 0)
            restored = task.restore_revision(target_revision)
            if restored is None:
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase=str(state["business_phase"]),
                    answer=(
                        f"当前不存在版本 v{target_revision}。"
                        if target_revision
                        else "当前没有可恢复的上一版文案。"
                    ),
                )
                return state
        errors = task.apply_turn_decision(
            decision,
            turn_id=str(state.get("active_turn_id", "")),
            confirmation_comment=str(state.get("user_message", "")),
        )
        if errors:
            return self._recover_turn(
                state,
                "本轮要求引用了不存在或无效的任务要求：" + "、".join(errors),
                "turn_decision_invalid",
            )
        self._trace(
            state,
            "turn_decision",
            "本轮意图和要求变更已形成唯一决策。",
            {
                "intent": intent,
                "summary": decision.get("summary", ""),
                "requirement_mutations": decision.get("requirement_mutations", []),
            },
        )
        state["task"] = task.to_snapshot()

        if decision.get("confirmation_resolutions"):
            state["confirmation_resolutions"] = [
                {
                    **dict(item),
                    "evidence_notes": (
                        str(state.get("user_message", ""))
                        if item.get("resolution") == "confirmed_with_basis"
                        else ""
                    ),
                }
                for item in decision["confirmation_resolutions"]
            ]
            state["route"] = "resolve_confirmation"
            return state

        if intent == "clarify":
            self._finish(
                state,
                task,
                status="waiting_user",
                phase="collect_context",
                answer=str(decision.get("question", "")),
                questions=[str(decision.get("question", ""))],
            )
        elif intent == "explain":
            self._finish(
                state,
                task,
                status="waiting_user",
                phase=str(state["business_phase"]),
                answer=str(decision.get("answer", "")),
            )
        elif intent == "compare":
            self._finish(
                state,
                task,
                status="waiting_user",
                phase=str(state["business_phase"]),
                answer=_compare_revisions(task),
            )
        elif intent == "restore":
            state["task"] = task.to_snapshot()
            state["route"] = "candidate_review"
        elif intent in {"draft", "revise"}:
            if task.missing_context():
                missing = "、".join(task.missing_context())
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase="collect_context",
                    answer=f"继续生成前还需要确认：{missing}。",
                    questions=[f"请补充 {missing}。"],
                )
            else:
                state["route"] = "draft_copy"
        elif intent == "review":
            if not task.current_draft:
                return self._recover_turn(state, "当前没有可审核的工作稿。", "review_input_missing")
            state["route"] = "candidate_review"
        elif intent in {"generate_image", "revise_image"}:
            if not self._copy_ready(task):
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase="draft_revision_needed",
                    answer="请先完成并确认当前文案，再生成宣传图。",
                )
            else:
                state["promotion_image_instruction"] = str(
                    state.get("user_message", "")
                ).strip()
                state["route"] = "plan_promotion_image"
        else:
            return self._recover_turn(state, f"不支持的本轮意图：{intent}", "unsupported_intent")
        return state

    def _resolve_confirmation(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        resolutions = list(state.get("confirmation_resolutions", []))
        if not task.pending_confirmation:
            self._finish(
                state,
                task,
                status="waiting_user",
                phase=str(state["business_phase"]),
                answer="当前没有等待处理的宣传事实，请直接说明修改、解释、比较或回退要求。",
            )
            return state
        if not resolutions:
            self._finish(
                state,
                task,
                status="waiting_user",
                phase="evidence_confirmation",
                answer="请为每一项宣传事实选择确认依据或风险优化。",
                questions=_confirmation_questions(task),
            )
            return state

        revision = task.record_confirmation_resolutions(resolutions)
        review = resolve_review_confirmations(
            task.current_review,
            resolutions=resolutions,
        )
        task.record_review(revision, review)
        task.pending_confirmation = {}
        actions = {
            str(item.get("action", ""))
            for item in review.get("decisions", [])
            if item.get("action") not in {"", "allow", "advisory"}
        }
        if actions:
            state["task"] = task.to_snapshot()
            state["route"] = "risk_rewrite"
        else:
            task.accept_revision(revision)
            self._finish(
                state,
                task,
                status="waiting_user",
                phase=self._business_phase(task),
                answer="已记录每项宣传事实的处理决定并保留确认内容。继续输入要求可以修改；留空提交将生成宣传图。",
            )
        return state

    def _draft_copy(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        decision = dict(state.get("decision", {}))
        draft_mode = str(state.get("draft_mode", "")).strip()
        removed_ids = {
            str(item.get("requirement_id", ""))
            for item in decision.get("requirement_mutations", [])
            if isinstance(item, dict) and item.get("operation") == "remove"
        }
        excluded_requirements = [
            item.source_text or item.text
            for item in task.requirements
            if item.requirement_id in removed_ids
        ]
        outcome = self.draft_tool.run(
            task,
            mode=draft_mode or ("revision" if task.current_draft else "generation"),
            instruction=str(decision.get("instruction", "")),
            excluded_requirements=excluded_requirements,
        )
        self._record_execution(
            state,
            outcome,
            tool_name=outcome.tool_name,
        )
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"本轮文案候选未生成：{outcome.error}",
                outcome.error_type,
            )
        task.stage_revision(
            str(outcome.parsed["primary_draft"]),
            source=("agent_generated" if draft_mode == "generation" or not task.current_draft else "agent_revised"),
            instruction=str(decision.get("instruction", "")),
        )
        state["draft_mode"] = ""
        state["task"] = task.to_snapshot()
        state["route"] = "candidate_review"
        return state

    def _candidate_review(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        current = task.current_revision_record
        revision = task.current_revision
        outcome = self.review_tool.run(task, content=task.current_draft, revision=revision)
        self._record_execution(
            state,
            outcome,
            tool_name=outcome.tool_name,
            tool_calls=outcome.tool_calls,
        )
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"本轮候选审核未完成：{outcome.error}",
                outcome.error_type,
            )
        review = outcome.review
        state["review_candidate"] = review
        state["task"] = task.to_snapshot()
        state["route"] = "apply_review_result"
        return state

    def _apply_review_result(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        revision = task.current_revision
        review = dict(state.get("review_candidate", {}))
        task.record_review(revision, review)
        self._trace(
            state,
            "canonical_review",
            "当前 Revision 已形成唯一审核决策。",
            {
                "revision": revision,
                "content": task.current_draft,
                "publication_conclusion": review.get("publication_conclusion", ""),
                "readiness_score": review.get("readiness_score", 0),
                "decisions": review.get("decisions", []),
            },
        )
        review_outcome = str(review.get("review_outcome", ""))
        unfulfilled_ids = list(review.get("unfulfilled_requirement_ids", []))
        current = task.current_revision_record
        previous_reviews = [
            item.review
            for item in task.revisions
            if item.status == "candidate" and item.revision < revision
        ]
        if (
            previous_reviews
            and review_outcome in {"safe", "needs_confirmation"}
        ):
            task.apply_reviewed_rewrites(previous_reviews)
        state["task"] = task.to_snapshot()
        if review_outcome == "needs_requirement_revision":
            if (
                current is not None
                and current.source == "restored"
            ) or bool(state.get("requirement_repair_attempted")):
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase="draft_revision_needed",
                    answer=(
                        "当前候选仍未完整落实这些要求："
                        + "、".join(unfulfilled_ids)
                        + "。候选和审核结果已保留，请继续说明希望如何调整。"
                    ),
                )
                return state
            requirements = {
                item.requirement_id: item.text for item in task.active_requirements
            }
            state["requirement_repair_attempted"] = True
            state["draft_mode"] = "revision"
            state["decision"] = {
                "intent": "revise",
                "instruction": "补齐当前候选未落实的用户要求："
                + "；".join(
                    f"{requirement_id}={requirements.get(requirement_id, requirement_id)}"
                    for requirement_id in unfulfilled_ids
                ),
            }
            state["task"] = task.to_snapshot()
            state["route"] = "draft_copy"
        elif review_outcome == "needs_more_context":
            question = str(review.get("summary", "")).strip() or "请补充当前审核所需的商品信息。"
            self._finish(
                state,
                task,
                status="waiting_user",
                phase="collect_context",
                answer=question,
                questions=[question],
            )
        elif review_outcome in {"needs_targeted_rewrite", "needs_full_redraft"}:
            if bool(state.get("full_redraft_attempted")) or (
                current is not None and current.source == "risk_optimized"
            ):
                self._finish(
                    state,
                    task,
                    status="waiting_user",
                    phase="draft_revision_needed",
                    answer=(
                        "当前风险优化候选复审后仍有必须处理的表达，尚未作为可发布版本生效。"
                        "你可以查看审核结果并继续说明修改要求。"
                    ),
                )
            elif review_outcome == "needs_full_redraft":
                return self._route_to_full_redraft(
                    state,
                    task,
                    "根据当前任务要求和审核结果重新起草完整工作稿，保留安全要求并处理全部风险。",
                    "candidate_requires_full_redraft",
                )
            else:
                state["route"] = "risk_rewrite"
        elif review_outcome == "needs_confirmation":
            task.set_pending_confirmation(revision, review)
            self._finish(
                state,
                task,
                status="waiting_user",
                phase="evidence_confirmation",
                answer="当前工作稿包含需要你确认依据的宣传事实。确认有真实依据可以保留，或接受风险改写。",
                questions=_confirmation_questions(task),
            )
        elif review_outcome == "safe":
            task.accept_revision(revision)
            risk_notice = _risk_adjustment_notice(task)
            target_revision = int(
                dict(state.get("decision", {})).get("target_revision", 0) or 0
            )
            restore_notice = (
                (
                    f"已恢复版本 v{target_revision} 的文案并按当前要求重新审核。"
                    if target_revision
                    else "已恢复上一版文案并按当前要求重新审核。"
                )
                if current and current.source == "restored"
                else ""
            )
            self._finish(
                state,
                task,
                status="waiting_user",
                phase=self._business_phase(task),
                answer=(
                    restore_notice
                    + risk_notice
                    + "当前工作稿已完成审核。继续输入要求可以修改；不输入内容直接提交将生成最终发布包。"
                ),
            )
        else:
            return self._recover_turn(
                state,
                f"审核工具返回了未知结果：{review_outcome}",
                "review_outcome_invalid",
            )
        return state

    def _risk_rewrite(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        source_review = task.current_review
        outcome = self.risk_rewrite_tool.run(task, review=source_review)
        self._record_execution(
            state,
            outcome,
            tool_name=outcome.tool_name,
        )
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"风险优化稿未生成：{outcome.error}",
                outcome.error_type,
            )
        revision = task.stage_revision(
            str(outcome.parsed["primary_draft"]),
            source="risk_optimized",
            instruction="根据审核的 canonical decisions 进行风险优化。",
        )
        self._trace(
            state,
            "risk_rewrite",
            "风险优化工具基于 canonical decisions 形成新候选。",
            {
                "revision": revision.revision,
                "content": revision.content,
                "decisions": source_review.get("decisions", []),
            },
        )
        state["task"] = task.to_snapshot()
        state["route"] = "candidate_review"
        return state

    def _route_to_full_redraft(
        self,
        state: ReleaseGraphState,
        task: ReleaseTask,
        instruction: str,
        error_type: str,
    ) -> ReleaseGraphState:
        state["full_redraft_attempted"] = True
        state["draft_mode"] = "generation"
        state["decision"] = {"intent": "revise", "instruction": instruction}
        self._trace(
            state,
            "bounded_full_redraft",
            "当前候选无法形成可验证结果，执行唯一一次完整重写。",
            {"error_type": error_type},
        )
        state["task"] = task.to_snapshot()
        state["route"] = "draft_copy"
        return state

    def _build_release_package(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        task.final_release_package = self.package_builder.build(task)
        task.add_event("release_package_created", revision=task.current_revision)
        self._finish(
            state,
            task,
            status="completed",
            phase="release_package_ready",
            answer="最终发布包已经生成，文案为当前确认版本。",
        )
        return state

    def _plan_promotion_image(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        outcome = self.promotion_image_prompt_tool.run(
            task,
            instruction=str(state.get("promotion_image_instruction", "")),
        )
        self._record_execution(state, outcome, tool_name=outcome.tool_name)
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"宣传图任务提示未生成：{outcome.error}",
                outcome.error_type,
            )
        state["promotion_image_plan"] = dict(outcome.parsed)
        state["route"] = "generate_promotion_image"
        return state

    def _generate_promotion_image(self, state: ReleaseGraphState) -> ReleaseGraphState:
        task = self._task(state)
        plan = dict(state.get("promotion_image_plan", {}))
        outcome = self.promotion_image_generation_tool.run(task, plan=plan)
        self._record_execution(state, outcome, tool_name=outcome.tool_name)
        if not outcome.ok:
            return self._recover_turn(
                state,
                f"宣传图未生成：{outcome.error}",
                outcome.error_type,
            )
        task.record_promotion_image(
            asset_id=str(outcome.parsed["asset_id"]),
            display_text=list(outcome.parsed["display_text"]),
            prompt=str(outcome.parsed["image_prompt"]),
            instruction=str(state.get("promotion_image_instruction", "")),
        )
        self._finish(
            state,
            task,
            status="waiting_user",
            phase="promotion_image_review_ready",
            answer="宣传图已经生成。满意可直接提交并生成最终交付包；不满意可说明希望调整的内容。",
        )
        return state

    def _recover_turn(
        self,
        state: ReleaseGraphState,
        reason: str,
        error_type: str,
    ) -> ReleaseGraphState:
        user_message = str(state.get("user_message", "")).strip()
        base = dict(state.get("base_task", {}))
        task = ReleaseTask.from_snapshot(str(state["task_id"]), base)
        if user_message:
            task.append_conversation("user", user_message)
        task.last_turn_error = {
            "error_type": error_type,
            "reason": reason,
        }
        task.add_event(
            "turn_not_applied",
            error_type=error_type,
            reason=reason,
        )
        self._finish(
            state,
            task,
            status="waiting_user",
            phase=self._business_phase(task),
            answer=(
                reason
                + " 已保留上一版任务状态。"
                + "你可以稍后重试，或继续说明要求。"
            ),
        )
        return state

    def _finish(
        self,
        state: ReleaseGraphState,
        task: ReleaseTask,
        *,
        status: str,
        phase: str,
        answer: str,
        questions: List[str] | None = None,
    ) -> None:
        task.state_version += 1
        task.append_conversation("assistant", answer, phase=phase, status=status)
        state.update(
            {
                "task": task.to_snapshot(),
                "status": status,
                "phase": phase,
                "answer": answer,
                "next_questions": list(questions or []),
                "route": "end",
                "last_completed_turn_id": str(state.get("active_turn_id", "")),
                "last_completed_turn_fingerprint": str(state.get("active_turn_fingerprint", "")),
                "active_turn_id": "",
                "active_turn_fingerprint": "",
            }
        )

    def _record_execution(
        self,
        state: ReleaseGraphState,
        outcome: ModelExecutionOutcome | ToolOutcome,
        *,
        tool_name: str,
        tool_calls: List[Dict[str, Any]] | None = None,
    ) -> None:
        attempts = outcome.attempts
        data = {
            "tool_name": tool_name,
            "status": "ok" if outcome.ok else "failed",
            "model_calls": attempts,
            "result_type": outcome.result_type,
        }
        if tool_calls is not None:
            data["tool_calls"] = tool_calls
        if tool_calls is not None:
            stage = "tool"
            message = "检索法规与平台知识并执行综合语义审核。"
        elif attempts:
            stage = "model"
            message = f"执行模型能力：{tool_name}"
        else:
            stage = "tool"
            message = f"执行确定性工具：{tool_name}"
        self._trace(state, stage, message, data)

    def _trace(
        self,
        state: ReleaseGraphState,
        stage: str,
        message: str,
        data: Dict[str, Any],
    ) -> None:
        traces = list(state.get("traces", []))
        traces.append({"stage": stage, "message": message, "data": data})
        state["traces"] = traces[-MAX_TRACE_EVENTS:]

    def _task(self, state: ReleaseGraphState) -> ReleaseTask:
        return ReleaseTask.from_snapshot(str(state["task_id"]), dict(state.get("task", {})))

    def _business_phase(self, task: ReleaseTask) -> str:
        if task.final_release_package:
            return "release_package_ready"
        if task.pending_confirmation:
            return "evidence_confirmation"
        if self._copy_ready(task):
            image_status = str(task.promotion_image.get("status", ""))
            if image_status == "stale":
                return "promotion_image_revision_needed"
            if image_status in {"awaiting_user", "accepted"}:
                return "promotion_image_review_ready"
            return "draft_review_ready"
        if task.current_draft:
            return "draft_revision_needed"
        if task.image_analysis.get("quality_level") == "retake_required":
            return "source_image_retake_required"
        if task.image_analysis:
            return "source_image_ready"
        return "collect_context"

    @staticmethod
    def _copy_ready(task: ReleaseTask) -> bool:
        return bool(
            task.current_revision_record
            and task.current_revision_record.status == "accepted"
            and task.current_review.get("publication_conclusion") == "safe_to_publish"
            and not task.pending_confirmation
        )

def _route(state: ReleaseGraphState) -> str:
    return str(state.get("route", "end"))


def _confirmation_questions(task: ReleaseTask) -> List[str]:
    return [
        str(item.get("reason", ""))
        for item in task.pending_confirmation.get("items", [])
        if str(item.get("reason", "")).strip()
    ][:4]


def _compare_revisions(task: ReleaseTask) -> str:
    visible = [item for item in task.revisions if item.status != "rejected"]
    if len(visible) < 2:
        return "当前只有一个有效文案版本，暂时无法比较。"
    previous, current = visible[-2], visible[-1]
    return (
        f"上一版 v{previous.revision}：{previous.content}\n\n"
        f"当前版 v{current.revision}：{current.content}\n\n"
        "你可以要求恢复上一版，或继续说明要保留和调整的表达。"
    )


def _risk_adjustment_notice(task: ReleaseTask) -> str:
    current = task.current_revision_record
    if current is None or current.source != "risk_optimized":
        return ""
    previous = task.revision_at(current.revision - 1)
    decisions = previous.review.get("decisions", []) if previous else []
    notices = []
    for decision in decisions[:3]:
        if decision.get("action") not in {"block", "rewrite"}:
            continue
        source_text = str(decision.get("matched_text", "")).strip()
        if not source_text:
            continue
        risk_label = str(decision.get("label", "")).strip() or "明确违规表达"
        notices.append(f"“{source_text}”（违规点：{risk_label}）")
    if not notices:
        return ""
    return (
        "已自动改写明确违规内容："
        + "；".join(notices)
        + "。请确认当前优化版本；满意可直接提交，不满意可说明原意或继续提出修改。"
    )
