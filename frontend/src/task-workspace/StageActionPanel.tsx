import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ListChecks,
  LoaderCircle,
  ShieldAlert,
} from "lucide-react";
import { conclusionLabels, platformLabels } from "../releasePresentation";
import type {
  ConfirmationResolution,
  ReleaseTaskResult,
  ReviewComparison,
  ReviewComparisonItem,
  ReviewReport,
} from "../types";

export type WorkspaceStage = "context" | "copy" | "image" | "delivery";

interface ConfirmationSelection {
  resolution: ConfirmationResolution["resolution"] | "";
  evidence_notes: string;
}

interface StageActionPanelProps {
  stage: WorkspaceStage;
  state: ReleaseTaskResult["state"];
  review: ReviewReport | null;
  comparison: ReviewComparison | null;
  questions: string[];
  confirmationSelections: Record<string, ConfirmationSelection>;
  busy: boolean;
  onConfirmationChange: (
    decisionId: string,
    update: Partial<Omit<ConfirmationSelection, "resolution"> & {
      resolution: ConfirmationResolution["resolution"];
    }>,
  ) => void;
  onAdvance: () => void;
}

export function StageActionPanel({
  stage,
  state,
  review,
  comparison,
  questions,
  confirmationSelections,
  busy,
  onConfirmationChange,
  onAdvance,
}: StageActionPanelProps) {
  const pendingConfirmation = Boolean(state.pending_confirmation);
  const conclusion = review?.publication_conclusion ?? "";
  const score = review?.readiness_score ?? 0;
  const relevant = review?.decisions.filter((item) => item.action !== "allow") ?? [];
  const displayedIssues = pendingConfirmation
    ? relevant.filter((item) => item.action !== "confirm")
    : relevant;
  const confirmationItems = state.pending_confirmation?.items ?? [];
  const confirmationComplete = confirmationItems.length > 0
    && confirmationItems.every(
      (item) => Boolean(confirmationSelections[item.decision_id]?.resolution),
    );
  const image = state.promotion_image;
  const action = pendingConfirmation
    ? { label: "提交逐项决定", disabled: !confirmationComplete }
    : stage === "copy" && conclusion === "safe_to_publish"
      ? { label: "确认文案并生成宣传图", disabled: false }
      : stage === "image" && image?.status === "awaiting_user"
        ? { label: "确认宣传图并生成图文素材", disabled: false }
        : stage === "image" && image?.status === "accepted"
          ? { label: "生成图文素材", disabled: false }
          : stage === "image"
            ? { label: "生成新宣传图", disabled: false }
            : null;

  return (
    <aside className="stage-panel">
      <div className="stage-panel-heading">
        <span className="eyebrow">当前阶段</span>
        <h2>{stagePanelTitle(stage, pendingConfirmation)}</h2>
        <p>{stagePanelDescription(stage, state)}</p>
      </div>

      {stage === "context" && (
        <dl className="task-facts">
          <div><dt>商品</dt><dd>{state.product_name || state.product_category || "等待识别"}</dd></div>
          <div><dt>平台</dt><dd>{platformLabels[state.platform] || state.platform || "等待确认"}</dd></div>
          <div><dt>有效要求</dt><dd>{state.active_requirements.length} 项</dd></div>
        </dl>
      )}

      {stage === "copy" && (
        <>
          {comparison?.previous && comparison.current ? (
            <div className="score-comparison">
              <ScoreCell label="上一轮" data={comparison.previous} />
              <span className="comparison-arrow">→</span>
              <ScoreCell label="当前轮" data={comparison.current} />
            </div>
          ) : conclusion ? (
            <div className={`score-summary ${conclusion}`}>
              <strong>{conclusionLabels[conclusion] || conclusion}</strong>
              <span>{score}</span>
              <small>发布准备度</small>
            </div>
          ) : (
            <div className="empty-content">当前版本尚未形成审核结论。</div>
          )}
          {relevant.length === 0 && conclusion === "safe_to_publish" && (
            <div className="review-safe">
              <CheckCircle2 size={17} />未发现需要阻止发布的明确风险
            </div>
          )}
          <div className="issue-list">
            {displayedIssues.slice(0, 6).map((item) => (
              <div className="issue" key={item.decision_id}>
                <strong>{item.label}</strong>
                {item.matched_text && <span>相关表达：{item.matched_text}</span>}
                <p>{item.reason}</p>
              </div>
            ))}
          </div>
          {review && (
            <details className="details-block">
              <summary>完整审核信息<ChevronDown size={15} /></summary>
              <ReviewDetails review={review} />
            </details>
          )}
        </>
      )}

      {stage === "image" && (
        <div className="image-stage-status">
          <span className={`status-badge ${image?.status === "stale" ? "warning" : image?.status === "accepted" ? "success" : "neutral"}`}>
            {promotionImageStatus(image?.status || "")}
          </span>
          {image?.display_text.length ? (
            <div><strong>画面宣传点</strong><p>{image.display_text.join(" · ")}</p></div>
          ) : null}
          {image?.instruction && (
            <div><strong>本轮调整</strong><p>{image.instruction}</p></div>
          )}
          {image?.status === "stale" && (
            <div className="review-warning">
              <AlertTriangle size={17} />
              <span>文案或要求已经变化，当前图片不会进入图文素材包。</span>
            </div>
          )}
        </div>
      )}

      {stage === "delivery" && state.final_release_package && (
        <div className="delivery-stage-status">
          <div className="review-safe">
            <CheckCircle2 size={17} />图文素材已经准备完成
          </div>
          <dl className="task-facts">
            <div><dt>文案版本</dt><dd>v{state.final_release_package.revision}</dd></div>
            <div><dt>发布准备度</dt><dd>{state.final_release_package.readiness_score}</dd></div>
            <div><dt>待核对项</dt><dd>{state.final_release_package.pending_items.length} 项</dd></div>
          </dl>
        </div>
      )}

      {pendingConfirmation && (
        <div className="confirmation-list">
          {confirmationItems.map((item, index) => {
            const selection = confirmationSelections[item.decision_id];
            return (
              <section className="confirmation-item" key={item.decision_id}>
                <div className="confirmation-item-heading">
                  <span>{index + 1}</span>
                  <div><strong>{item.matched_text || item.label}</strong><p>{item.reason}</p></div>
                </div>
                <div className="confirmation-options">
                  <label className={selection?.resolution === "confirmed_with_basis" ? "selected" : ""}>
                    <input
                      type="radio"
                      name={`confirmation-${item.decision_id}`}
                      value="confirmed_with_basis"
                      checked={selection?.resolution === "confirmed_with_basis"}
                      onChange={() => onConfirmationChange(item.decision_id, { resolution: "confirmed_with_basis" })}
                    />
                    <CheckCircle2 size={18} />
                    <span><strong>确有真实依据</strong><small>保留这一项宣传事实</small></span>
                  </label>
                  <label className={selection?.resolution === "rewrite_without_basis" ? "selected" : ""}>
                    <input
                      type="radio"
                      name={`confirmation-${item.decision_id}`}
                      value="rewrite_without_basis"
                      checked={selection?.resolution === "rewrite_without_basis"}
                      onChange={() => onConfirmationChange(item.decision_id, {
                        resolution: "rewrite_without_basis",
                        evidence_notes: "",
                      })}
                    />
                    <ShieldAlert size={18} />
                    <span><strong>接受风险改写</strong><small>仅优化这一项表达</small></span>
                  </label>
                </div>
                {selection?.resolution === "confirmed_with_basis" && (
                  <input
                    className="evidence-input"
                    aria-label={`${item.matched_text || item.label}的核验说明`}
                    value={selection.evidence_notes}
                    onChange={(event) => onConfirmationChange(item.decision_id, {
                      evidence_notes: event.target.value,
                    })}
                    placeholder="可选：填写证明材料或核验说明"
                  />
                )}
              </section>
            );
          })}
        </div>
      )}

      {questions.length > 0 && stage === "context" && (
        <div className="stage-questions">
          <strong>还需要补充</strong>
          {questions.map((item) => <p key={item}>{item}</p>)}
        </div>
      )}
      {action && (
        <button
          className="primary-button stage-primary-action"
          disabled={busy || action.disabled}
          onClick={onAdvance}
        >
          {busy ? (
            <><LoaderCircle className="spin" size={17} />Agent 正在处理</>
          ) : (
            <><ListChecks size={17} />{action.label}</>
          )}
        </button>
      )}
      {!action && (
        <p className="stage-guidance">
          {stage === "delivery"
            ? "需要调整时，在左侧继续说明；原图文素材会在内容变化后自动失效。"
            : "在左侧向 Agent 补充信息或说明修改要求。"}
        </p>
      )}
    </aside>
  );
}

function ScoreCell({ label, data }: { label: string; data: ReviewComparisonItem }) {
  return (
    <div>
      <small>{label} · v{data.revision}</small>
      <strong>{conclusionLabels[data.publication_conclusion] || data.publication_conclusion}</strong>
      <span>{data.readiness_score}</span>
    </div>
  );
}

function ReviewDetails({ review }: { review: ReviewReport }) {
  return (
    <div className="review-details">
      {review.summary && <p>{review.summary}</p>}
      <p><strong>处理建议：</strong>{review.publication_action}</p>
    </div>
  );
}

function stagePanelTitle(stage: WorkspaceStage, pendingConfirmation: boolean): string {
  if (pendingConfirmation) return "确认宣传事实";
  return {
    context: "补齐任务信息",
    copy: "审核并确认文案",
    image: "确认宣传图",
    delivery: "图文素材已完成",
  }[stage];
}

function stagePanelDescription(
  stage: WorkspaceStage,
  state: ReleaseTaskResult["state"],
): string {
  if (stage === "context") return "Agent 正在整理商品依据和发布要求。";
  if (stage === "copy") return "查看当前版本的风险、评分和要求落实情况。";
  if (stage === "image") {
    return state.promotion_image?.status === "stale"
      ? "当前图片已过期，需要按最新内容重新生成。"
      : "检查当前图片，满意后确认进入最终交付。";
  }
  return "文案、宣传图和交付检查已汇总到当前交付版本。";
}

function promotionImageStatus(status: string): string {
  return {
    awaiting_user: "等待你的确认",
    accepted: "已确认宣传图",
    stale: "宣传图已过期",
  }[status] || "等待生成";
}
