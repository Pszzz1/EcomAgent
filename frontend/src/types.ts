export type JsonObject = Record<string, unknown>;

export interface TraceEvent {
  stage: string;
  message: string;
  data: JsonObject;
}

export interface Requirement {
  requirement_id: string;
  text: string;
  source_text: string;
  kind: string;
  status: string;
  source: string;
  created_turn: string;
}

export interface ReviewDecision {
  decision_id: string;
  origin: "requirement" | "draft_generated";
  requirement_id: string;
  matched_text: string;
  label: string;
  risk_family: string;
  severity: "low" | "medium" | "high";
  reason: string;
  confirmation_resolution?: "" | "confirmed_with_basis" | "rewrite_without_basis";
  human_confirmation_eligible: boolean;
  action: "allow" | "advisory" | "block" | "rewrite" | "confirm";
}

export interface ReviewReport {
  revision: number;
  content: string;
  publication_conclusion: "safe_to_publish" | "revise_before_publish" | "prohibit_publish";
  publication_action: "allow" | "revise_required" | "block_directly" | "human_review_required";
  review_outcome: "safe" | "needs_targeted_rewrite" | "needs_full_redraft" | "needs_requirement_revision" | "needs_confirmation" | "needs_more_context";
  readiness_score: number;
  summary: string;
  decisions: ReviewDecision[];
  unfulfilled_requirement_ids: string[];
  human_confirmation_items: ReviewDecision[];
}

export interface Revision {
  revision: number;
  content: string;
  source: string;
  instruction: string;
  status: string;
  review: ReviewReport | null;
}

export interface ReviewComparisonItem {
  revision: number;
  publication_conclusion: string;
  readiness_score: number;
}

export interface ReviewComparison {
  previous: ReviewComparisonItem;
  current: ReviewComparisonItem;
}

export interface PendingConfirmation {
  revision: number;
  items: ReviewDecision[];
  review: ReviewReport;
}

export interface ConfirmationResolution {
  decision_id: string;
  resolution: "confirmed_with_basis" | "rewrite_without_basis";
  evidence_notes: string;
}

export interface ConfirmedEvidence {
  requirement_id: string;
  requirement_source_text: string;
  matched_text: string;
  risk_family: string;
  decision: string;
  comment: string;
}

export interface ConversationEntry {
  role: "user" | "assistant";
  content: string;
  phase: string;
  status: string;
  decision: string;
}

export interface PlatformContent {
  platform: string;
  title: string;
  body: string;
  script: string;
}

export interface ReleasePackage {
  package_status: "ready_to_publish";
  platform: string;
  product_name: string;
  product_category: string;
  revision: number;
  risk_status: "safe_to_publish";
  readiness_score: number;
  review_summary: string;
  final_copy: string;
  promotion_image_asset_id: string;
  promotion_image_text: string[];
  platform_content: PlatformContent;
  requirement_delivery: Array<{
    requirement_id: string;
    requirement: string;
    status: string;
  }>;
  review_decisions: ReviewDecision[];
  confirmed_evidence: ConfirmedEvidence[];
  pending_items: string[];
  publish_checklist: string[];
}

export interface PromotionImage {
  asset_id: string;
  display_text: string[];
  prompt: string;
  instruction: string;
  copy_revision: number;
  status: "awaiting_user" | "accepted" | "stale";
}

export interface ReleaseTaskState {
  schema_version: number;
  task_id: string;
  task_brief: string;
  product_name: string;
  product_category: string;
  platform: string;
  objective: string;
  source_image_asset_id: string;
  image_analysis: JsonObject;
  promotion_image: PromotionImage | null;
  requirements: Requirement[];
  active_requirements: Requirement[];
  revisions: Revision[];
  current_revision: number;
  current_draft: string;
  draft_origin: string;
  current_review: ReviewReport | null;
  review_comparison: ReviewComparison | null;
  pending_confirmation: PendingConfirmation | null;
  confirmed_evidence: ConfirmedEvidence[];
  conversation: ConversationEntry[];
  events: Array<Record<string, unknown>>;
  final_release_package: ReleasePackage | null;
  last_turn_error: { error_type: string; reason: string } | null;
  state_version: number;
  next_requirement_number: number;
}

export interface ReleaseTaskResult {
  task_id: string;
  status: string;
  phase: string;
  answer: string;
  next_questions: string[];
  state: ReleaseTaskState;
  trace_events: TraceEvent[];
}

export interface ReleaseTaskSummary {
  task_id: string;
  status: string;
  phase: string;
  product_name: string;
  product_category: string;
  platform: string;
  current_revision: number;
  updated_at: string;
}

export interface ContinueTaskInput {
  message: string;
  confirmation_resolutions: ConfirmationResolution[];
  turn_id: string;
  expected_state_version?: number;
}
