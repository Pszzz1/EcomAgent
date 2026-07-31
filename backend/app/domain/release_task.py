from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List


STATE_SCHEMA_VERSION = 13


@dataclass
class Requirement:
    requirement_id: str
    text: str
    source_text: str = ""
    kind: str = "content"
    status: str = "active"
    source: str = "user"
    created_turn: str = ""

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Requirement":
        return cls(
            requirement_id=str(value["requirement_id"]),
            text=str(value["text"]),
            source_text=str(
                value.get("source_text") or value.get("text", "")
            ),
            kind=str(value.get("kind", "content")),
            status=str(value.get("status", "active")),
            source=str(value.get("source", "user")),
            created_turn=str(value.get("created_turn", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_context_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "text": self.text,
            "kind": self.kind,
        }


@dataclass
class Revision:
    revision: int
    content: str
    source: str
    instruction: str = ""
    status: str = "candidate"
    review: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Revision":
        return cls(
            revision=int(value["revision"]),
            content=str(value["content"]),
            source=str(value.get("source", "agent_generated")),
            instruction=str(value.get("instruction", "")),
            status=str(value.get("status", "candidate")),
            review=dict(value.get("review", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReleaseTask:
    task_id: str
    task_brief: str = ""
    product_name: str = ""
    product_category: str = ""
    platform: str = ""
    objective: str = ""
    source_image_asset_id: str = ""
    image_analysis: Dict[str, Any] = field(default_factory=dict)
    promotion_image: Dict[str, Any] = field(default_factory=dict)
    requirements: List[Requirement] = field(default_factory=list)
    revisions: List[Revision] = field(default_factory=list)
    current_revision: int = 0
    pending_confirmation: Dict[str, Any] = field(default_factory=dict)
    confirmed_evidence: List[Dict[str, Any]] = field(default_factory=list)
    conversation: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    final_release_package: Dict[str, Any] = field(default_factory=dict)
    last_turn_error: Dict[str, Any] = field(default_factory=dict)
    state_version: int = 0
    next_requirement_number: int = 1

    @classmethod
    def from_input(cls, task_id: str, item: Any) -> "ReleaseTask":
        task = cls(
            task_id=task_id,
            task_brief=str(item.task_brief).strip(),
            product_name=str(item.product_name).strip(),
            product_category=str(item.product_category).strip(),
            platform=str(item.platform).strip(),
            objective=str(item.objective).strip(),
            source_image_asset_id=str(item.source_image_asset_id).strip(),
        )
        for text in item.selling_points:
            task.add_requirement(str(text), kind="selling_point", created_turn="create")
        for text in item.brand_constraints:
            task.add_requirement(str(text), kind="content", created_turn="create")
        if str(item.draft_copy).strip():
            task.stage_revision(
                str(item.draft_copy).strip(),
                source="user_provided",
                instruction="用户创建任务时提供的草稿。",
            )
        return task

    @classmethod
    def from_snapshot(cls, task_id: str, state: Dict[str, Any]) -> "ReleaseTask":
        if int(state.get("schema_version", 0) or 0) != STATE_SCHEMA_VERSION:
            raise ValueError("Stored release task uses an unsupported state schema.")
        return cls(
            task_id=task_id,
            task_brief=str(state.get("task_brief", "")),
            product_name=str(state.get("product_name", "")),
            product_category=str(state.get("product_category", "")),
            platform=str(state.get("platform", "")),
            objective=str(state.get("objective", "")),
            source_image_asset_id=str(state.get("source_image_asset_id", "")),
            image_analysis=dict(state.get("image_analysis", {})),
            promotion_image=dict(state.get("promotion_image", {})),
            requirements=[
                Requirement.from_dict(item)
                for item in state.get("requirements", [])
                if isinstance(item, dict)
            ],
            revisions=[
                Revision.from_dict(item)
                for item in state.get("revisions", [])
                if isinstance(item, dict)
            ],
            current_revision=int(state.get("current_revision", 0) or 0),
            pending_confirmation=dict(state.get("pending_confirmation", {})),
            confirmed_evidence=[
                dict(item)
                for item in state.get("confirmed_evidence", [])
                if isinstance(item, dict)
            ],
            conversation=[
                dict(item)
                for item in state.get("conversation", [])
                if isinstance(item, dict)
            ],
            events=[dict(item) for item in state.get("events", []) if isinstance(item, dict)],
            final_release_package=dict(state.get("final_release_package", {})),
            last_turn_error=dict(state.get("last_turn_error", {})),
            state_version=int(state.get("state_version", 0) or 0),
            next_requirement_number=int(state.get("next_requirement_number", 1) or 1),
        )

    @property
    def active_requirements(self) -> List[Requirement]:
        return [item for item in self.requirements if item.status == "active"]

    @property
    def current_revision_record(self) -> Revision | None:
        return self.revision_at(self.current_revision)

    @property
    def current_draft(self) -> str:
        current = self.current_revision_record
        return current.content if current else ""

    @property
    def current_review(self) -> Dict[str, Any]:
        current = self.current_revision_record
        return dict(current.review) if current else {}

    @property
    def restorable_revisions(self) -> List[Revision]:
        return [
            item
            for item in self.revisions
            if item.revision < self.current_revision
        ]

    def revision_at(self, revision: int) -> Revision | None:
        return next((item for item in self.revisions if item.revision == revision), None)

    def pending_compliance_requirement_ids(self) -> set[str]:
        current = self.current_revision_record
        if current is None or current.source not in {"risk_optimized", "agent_generated"}:
            return set()
        return {
            str(decision.get("requirement_id", ""))
            for revision in self.revisions
            if revision.status == "candidate" and revision.revision < current.revision
            for decision in revision.review.get("decisions", [])
            if decision.get("action") in {"rewrite", "block"}
            and str(decision.get("requirement_id", ""))
        }

    def add_requirement(
        self,
        text: str,
        *,
        kind: str = "content",
        source: str = "user",
        created_turn: str = "",
        source_text: str = "",
    ) -> Requirement | None:
        normalized = text.strip()
        if not normalized:
            return None
        existing = next(
            (item for item in self.active_requirements if item.text == normalized),
            None,
        )
        if existing:
            return existing
        requirement = Requirement(
            requirement_id=f"req-{self.next_requirement_number}",
            text=normalized,
            source_text=source_text.strip() or normalized,
            kind=kind if kind in {"fact", "selling_point", "style", "content"} else "content",
            source=source,
            created_turn=created_turn,
        )
        self.next_requirement_number += 1
        self.requirements.append(requirement)
        return requirement

    def apply_turn_decision(
        self,
        decision: Dict[str, Any],
        *,
        turn_id: str,
        confirmation_comment: str = "",
    ) -> List[str]:
        errors: List[str] = []
        changed = False
        updates = decision.get("task_updates", {})
        if isinstance(updates, dict):
            for field_name in ("product_name", "product_category", "platform", "objective"):
                if field_name in updates and str(updates[field_name]).strip():
                    value = str(updates[field_name]).strip()
                    if field_name == "platform":
                        value = _canonical_platform(value)
                    if getattr(self, field_name) != value:
                        setattr(self, field_name, value)
                        changed = True

        by_id = {item.requirement_id: item for item in self.requirements}
        for requirement_id in decision.get("reactivate_requirement_ids", []):
            requirement = by_id.get(str(requirement_id))
            if requirement is None or requirement.status != "removed_for_compliance":
                errors.append(f"requirement_not_reactivatable:{requirement_id}")
                continue
            requirement.status = "active"
            previous_decision = next(
                (
                    review_decision
                    for revision in reversed(self.revisions)
                    for review_decision in revision.review.get("decisions", [])
                    if str(review_decision.get("requirement_id", "")) == requirement.requirement_id
                ),
                {},
            )
            self.confirmed_evidence = [
                item
                for item in self.confirmed_evidence
                if str(item.get("requirement_id", "")) != requirement.requirement_id
            ]
            self.confirmed_evidence.append(
                {
                    "requirement_id": requirement.requirement_id,
                    "requirement_source_text": requirement.source_text,
                    "matched_text": str(previous_decision.get("matched_text", "")) or requirement.text,
                    "risk_family": str(previous_decision.get("risk_family", "")),
                    "decision": "confirmed_with_basis",
                    "comment": confirmation_comment.strip(),
                }
            )
            changed = True
        for mutation in decision.get("requirement_mutations", []):
            if not isinstance(mutation, dict):
                continue
            operation = str(mutation.get("operation", "add"))
            requirement_id = str(mutation.get("requirement_id", ""))
            text = str(mutation.get("source_text", "")).strip()
            if operation == "add":
                if not text:
                    errors.append("add_requirement_requires_text")
                    continue
                self.add_requirement(
                    text,
                    kind=str(mutation.get("kind", "content")),
                    created_turn=turn_id,
                    source_text=text,
                )
                changed = True
            elif operation == "remove":
                requirement = by_id.get(requirement_id)
                if requirement is None:
                    errors.append(f"unknown_requirement:{requirement_id}")
                    continue
                requirement.status = "removed"
                changed = True
            else:
                errors.append(f"unsupported_requirement_operation:{operation}")
        if changed:
            self.final_release_package = {}
            self.invalidate_promotion_image()
        return errors

    def stage_revision(
        self,
        content: str,
        *,
        source: str,
        instruction: str,
    ) -> Revision:
        normalized = content.strip()
        if not normalized:
            raise ValueError("A revision cannot be empty.")
        revision = Revision(
            revision=max((item.revision for item in self.revisions), default=0) + 1,
            content=normalized,
            source=source,
            instruction=instruction,
        )
        self.revisions.append(revision)
        self.current_revision = revision.revision
        self.final_release_package = {}
        self.invalidate_promotion_image()
        self.add_event("revision_staged", revision=revision.revision, source=source)
        return revision

    def replace_source_image(self, asset_id: str) -> None:
        normalized = asset_id.strip()
        if not normalized:
            raise ValueError("A replacement source image is required.")
        self.source_image_asset_id = normalized
        self.image_analysis = {}
        self.final_release_package = {}
        self.invalidate_promotion_image()
        self.add_event("source_image_replaced")

    def record_promotion_image(
        self,
        *,
        asset_id: str,
        display_text: List[str],
        prompt: str,
        instruction: str,
    ) -> None:
        self.promotion_image = {
            "asset_id": asset_id,
            "display_text": list(display_text),
            "prompt": prompt,
            "instruction": instruction,
            "copy_revision": self.current_revision,
            "status": "awaiting_user",
        }
        self.final_release_package = {}
        self.add_event("promotion_image_generated", revision=self.current_revision)

    def accept_promotion_image(self) -> None:
        if self.promotion_image.get("status") != "awaiting_user":
            raise ValueError("No promotion image is waiting for confirmation.")
        self.promotion_image["status"] = "accepted"
        self.add_event("promotion_image_accepted", revision=self.current_revision)

    def invalidate_promotion_image(self) -> None:
        if self.promotion_image:
            self.promotion_image["status"] = "stale"

    def record_review(self, revision: int, review: Dict[str, Any]) -> None:
        revision_record = self.revision_at(revision)
        if revision_record is None:
            raise ValueError("Review references an unknown revision.")
        if str(review.get("content", "")) != revision_record.content:
            raise ValueError("Review content does not match its revision.")
        revision_record.review = dict(review)
        self.add_event(
            "revision_reviewed",
            revision=revision,
            conclusion=str(review.get("publication_conclusion", "")),
        )

    def apply_reviewed_rewrites(
        self,
        previous_reviews: Iterable[Dict[str, Any]],
    ) -> None:
        targets = {
            str(item.get("requirement_id", ""))
            for review in previous_reviews
            for item in review.get("decisions", [])
            if item.get("action") in {"rewrite", "block"}
            and str(item.get("requirement_id", ""))
        }
        requirements = {item.requirement_id: item for item in self.active_requirements}
        for requirement_id in targets:
            requirement = requirements.get(requirement_id)
            if requirement is not None:
                requirement.status = "removed_for_compliance"

    def accept_revision(self, revision: int) -> Revision:
        revision_record = self.revision_at(revision)
        if (
            revision_record is None
            or revision_record.review.get("publication_conclusion") != "safe_to_publish"
        ):
            raise ValueError("Only a reviewed safe revision can become current.")
        for item in self.revisions:
            if item.status == "accepted":
                item.status = "superseded"
            elif item.status == "candidate" and item.revision != revision:
                item.status = "rejected"
        revision_record.status = "accepted"
        self.current_revision = revision
        self.pending_confirmation = {}
        self.last_turn_error = {}
        self.add_event("revision_accepted", revision=revision)
        return revision_record

    def reject_turn_revisions(self, base_revision: int, reason: str) -> None:
        for item in self.revisions:
            if item.revision > base_revision and item.status == "candidate":
                item.status = "rejected"
        if base_revision and self.revision_at(base_revision):
            self.current_revision = base_revision
        self.pending_confirmation = {}
        self.last_turn_error = {"reason": reason}
        self.add_event("turn_candidate_rejected", reason=reason)

    def restore_revision(self, target_revision: int = 0) -> Revision | None:
        restorable = self.restorable_revisions
        if target_revision:
            previous = next(
                (item for item in restorable if item.revision == target_revision),
                None,
            )
            if previous is None:
                return None
        else:
            if not restorable:
                return None
            previous = restorable[-1]
        restored = self.stage_revision(
            previous.content,
            source="restored",
            instruction=f"恢复版本 v{previous.revision}",
        )
        return restored

    def set_pending_confirmation(self, revision: int, review: Dict[str, Any]) -> None:
        items = [
            dict(item)
            for item in review.get("decisions", [])
            if isinstance(item, dict) and item.get("action") == "confirm"
        ]
        self.pending_confirmation = {
            "revision": revision,
            "items": items,
            "review": dict(review),
        }

    def record_confirmation_resolutions(
        self,
        resolutions: List[Dict[str, Any]],
    ) -> int:
        if not self.pending_confirmation:
            raise ValueError("No confirmation is pending.")
        expected_items = {
            str(item.get("decision_id", "")): item
            for item in self.pending_confirmation.get("items", [])
        }
        resolution_by_id: Dict[str, Dict[str, Any]] = {}
        for resolution in resolutions:
            decision_id = str(resolution.get("decision_id", ""))
            if not decision_id or decision_id in resolution_by_id:
                raise ValueError("Confirmation decisions must be unique and non-empty.")
            resolution_by_id[decision_id] = resolution
        if set(resolution_by_id) != set(expected_items):
            raise ValueError("Every pending confirmation item requires one decision.")

        revision = int(self.pending_confirmation["revision"])
        for decision_id, resolution in resolution_by_id.items():
            if resolution.get("resolution") == "confirmed_with_basis":
                item = expected_items[decision_id]
                requirement_id = str(item.get("requirement_id", ""))
                requirement = next(
                    (
                        candidate
                        for candidate in self.active_requirements
                        if candidate.requirement_id == requirement_id
                    ),
                    None,
                )
                self.confirmed_evidence.append(
                    {
                        "requirement_id": requirement_id,
                        "requirement_source_text": (
                            requirement.source_text if requirement is not None else ""
                        ),
                        "matched_text": str(item.get("matched_text", "")),
                        "risk_family": str(item.get("risk_family", "")),
                        "decision": "confirmed_with_basis",
                        "comment": str(resolution.get("evidence_notes", "")).strip(),
                    }
                )
        return revision

    def append_conversation(self, role: str, content: str, **metadata: Any) -> None:
        if content.strip():
            self.conversation.append({"role": role, "content": content.strip(), **metadata})
            self.conversation = self.conversation[-30:]

    def add_event(self, event: str, **data: Any) -> None:
        self.events.append({"event": event, **data})
        self.events = self.events[-100:]

    def missing_context(self) -> List[str]:
        missing = []
        if not self.platform:
            missing.append("platform")
        if not self.product_name and not self.product_category:
            missing.append("product")
        return missing

    def review_comparison(self) -> Dict[str, Any]:
        reviewed = [item for item in self.revisions if item.review]
        if len(reviewed) < 2:
            return {}
        previous, current = reviewed[-2], reviewed[-1]
        return {
            "previous": _review_comparison_item(previous),
            "current": _review_comparison_item(current),
        }

    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "task_id": self.task_id,
            "task_brief": self.task_brief,
            "product_name": self.product_name,
            "product_category": self.product_category,
            "platform": self.platform,
            "objective": self.objective,
            "source_image_asset_id": self.source_image_asset_id,
            "image_analysis": dict(self.image_analysis),
            "promotion_image": dict(self.promotion_image),
            "requirements": [item.to_dict() for item in self.requirements],
            "revisions": [item.to_dict() for item in self.revisions],
            "current_revision": self.current_revision,
            "pending_confirmation": dict(self.pending_confirmation),
            "confirmed_evidence": [dict(item) for item in self.confirmed_evidence],
            "conversation": [dict(item) for item in self.conversation],
            "events": [dict(item) for item in self.events],
            "final_release_package": dict(self.final_release_package),
            "last_turn_error": dict(self.last_turn_error),
            "state_version": self.state_version,
            "next_requirement_number": self.next_requirement_number,
        }

    def to_public_state(self) -> Dict[str, Any]:
        current = self.current_revision_record
        return {
            **self.to_snapshot(),
            "active_requirements": [item.to_dict() for item in self.active_requirements],
            "current_draft": self.current_draft,
            "draft_origin": current.source if current else "",
            "current_review": self.current_review,
            "review_comparison": self.review_comparison(),
        }


def _review_comparison_item(revision: Revision) -> Dict[str, Any]:
    return {
        "revision": revision.revision,
        "publication_conclusion": str(revision.review.get("publication_conclusion", "")),
        "readiness_score": int(revision.review.get("readiness_score", 0) or 0),
    }


def _canonical_platform(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "抖音": "douyin",
        "快手": "kuaishou",
        "小红书": "xiaohongshu",
    }.get(normalized, normalized)
