from __future__ import annotations

from typing import Any, Dict

from backend.app.domain import ReleaseTask


class ReleasePackageBuilder:
    def build(self, task: ReleaseTask) -> Dict[str, Any]:
        revision = task.current_revision_record
        if (
            revision is None
            or revision.status != "accepted"
            or revision.review.get("publication_conclusion") != "safe_to_publish"
        ):
            raise ValueError("Final package requires the current accepted safe revision.")
        if task.promotion_image.get("status") != "accepted":
            raise ValueError("Final package requires a confirmed promotion image.")
        review = revision.review
        return {
            "package_status": "ready_to_publish",
            "platform": task.platform,
            "product_name": task.product_name,
            "product_category": task.product_category,
            "revision": revision.revision,
            "risk_status": "safe_to_publish",
            "readiness_score": int(review.get("readiness_score", 0) or 0),
            "review_summary": str(review.get("summary", "")),
            "final_copy": revision.content,
            "promotion_image_asset_id": str(task.promotion_image["asset_id"]),
            "promotion_image_text": list(task.promotion_image.get("display_text", [])),
            "platform_content": {
                "platform": task.platform,
                "title": "",
                "body": revision.content,
                "script": "",
            },
            "requirement_delivery": [
                {
                    "requirement_id": item.requirement_id,
                    "requirement": item.text,
                    "status": "fulfilled",
                }
                for item in task.active_requirements
            ],
            "review_decisions": [dict(item) for item in review.get("decisions", [])],
            "confirmed_evidence": [dict(item) for item in task.confirmed_evidence],
            "pending_items": [],
            "publish_checklist": ["发布前再次核对商品参数、价格和库存等实时信息。"],
        }
