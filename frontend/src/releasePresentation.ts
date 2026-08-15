export const platformLabels: Record<string, string> = {
  douyin: "抖音",
  kuaishou: "快手",
  xiaohongshu: "小红书",
};

export const phaseLabels: Record<string, string> = {
  collect_context: "等待补充信息",
  source_image_ready: "商品图已识别",
  source_image_retake_required: "需要更换商品图",
  evidence_confirmation: "等待事实确认",
  draft_review_ready: "工作稿待确认",
  draft_revision_needed: "工作稿需要修改",
  promotion_image_review_ready: "宣传图待确认",
  promotion_image_revision_needed: "宣传图需要重做",
  release_package_ready: "图文素材已准备",
};

export const conclusionLabels: Record<string, string> = {
  safe_to_publish: "安全可发",
  revise_before_publish: "建议修改",
  prohibit_publish: "禁止发布",
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作未完成，请稍后重试。";
}

export function phaseTone(phase: string, status: string, hasTurnError = false): string {
  if (status === "failed" || hasTurnError) return "danger";
  if (phase === "evidence_confirmation" || phase === "collect_context") return "warning";
  if (phase === "release_package_ready") return "success";
  return "neutral";
}
