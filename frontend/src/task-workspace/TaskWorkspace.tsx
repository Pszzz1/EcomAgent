import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  Copy,
  Download,
  FileText,
  History,
  Image as ImageIcon,
  ImageUp,
  LoaderCircle,
  MessageSquare,
  RotateCcw,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import { releaseApi } from "../api";
import {
  conclusionLabels,
  errorMessage,
  phaseTone,
  platformLabels,
} from "../releasePresentation";
import type {
  ConfirmationResolution,
  ConversationEntry,
  ReleasePackage,
  ReleaseTaskResult,
  Revision,
} from "../types";
import {
  StageActionPanel,
  type WorkspaceStage,
} from "./StageActionPanel";

export function TaskWorkspace({ result }: { result: ReleaseTaskResult }) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState("");
  const [confirmationSelections, setConfirmationSelections] = useState<
    Record<string, { resolution: ConfirmationResolution["resolution"] | ""; evidence_notes: string }>
  >({});
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const state = result.state;
  const review = state.current_review;
  const revisions = state.revisions;
  const currentRevision = state.current_revision;
  const currentRecord = revisions.find((item) => item.revision === currentRevision);
  const promotionImage = state.promotion_image;
  const stage = workspaceStage(result.phase);

  useEffect(() => {
    setMessage("");
    setConfirmationSelections({});
  }, [result.task_id, result.state.state_version, result.phase]);

  const continueMutation = useMutation({
    mutationFn: (payload: { message: string; confirmation_resolutions: ConfirmationResolution[] }) =>
      releaseApi.continue(result.task_id, {
        message: payload.message,
        confirmation_resolutions: payload.confirmation_resolutions,
        turn_id: crypto.randomUUID(),
        expected_state_version: state.state_version,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["release-task", result.task_id], updated);
      queryClient.invalidateQueries({ queryKey: ["release-tasks"] });
    },
  });

  const replaceImageMutation = useMutation({
    mutationFn: (image: File) => releaseApi.replaceImage(
      result.task_id,
      image,
      state.state_version,
    ),
    onSuccess: (updated) => {
      queryClient.setQueryData(["release-task", result.task_id], updated);
      queryClient.invalidateQueries({ queryKey: ["release-tasks"] });
    },
  });

  function submitMessage(event: FormEvent) {
    event.preventDefault();
    if (!message.trim()) return;
    continueMutation.mutate({ message: message.trim(), confirmation_resolutions: [] });
  }

  function advanceStage() {
    const confirmation_resolutions = (state.pending_confirmation?.items ?? []).map((item) => {
      const selection = confirmationSelections[item.decision_id];
      return {
        decision_id: item.decision_id,
        resolution: selection.resolution as ConfirmationResolution["resolution"],
        evidence_notes: selection.evidence_notes.trim(),
      };
    });
    continueMutation.mutate({ message: "", confirmation_resolutions });
  }

  function restoreRevision(revision: number) {
    continueMutation.mutate({ message: `恢复到版本 v${revision} 的文案`, confirmation_resolutions: [] });
  }

  function updateConfirmation(
    decisionId: string,
    update: Partial<{ resolution: ConfirmationResolution["resolution"]; evidence_notes: string }>,
  ) {
    setConfirmationSelections((current) => ({
      ...current,
      [decisionId]: {
        resolution: current[decisionId]?.resolution ?? "",
        evidence_notes: current[decisionId]?.evidence_notes ?? "",
        ...update,
      },
    }));
  }

  const title = state.product_name || state.product_category || "未命名发布任务";
  const activeRequirements = state.active_requirements.filter((item) => item.status === "active");

  return (
    <div className="workspace">
      <section className="task-heading">
        <div>
          <span className="eyebrow">{platformLabels[state.platform ?? ""] || state.platform || "平台待确认"}</span>
          <h1>{title}</h1>
          <div className="task-tags">
            {state.product_category && <span>{state.product_category}</span>}
            <span>当前版本 v{currentRevision}</span>
            {state.draft_origin && <span>{draftOriginLabel(state.draft_origin)}</span>}
          </div>
        </div>
        <div className="task-heading-actions">
          <button className="icon-button" title="运行诊断" onClick={() => setDiagnosticsOpen(true)}><Activity size={18} /></button>
        </div>
      </section>

      <TaskProgress currentStage={stage} />
      {result.answer && <div className={`agent-notice ${phaseTone(result.phase, result.status, Boolean(state.last_turn_error))}`}><MessageSquare size={18} /><div><strong>Agent</strong><p>{result.answer}</p></div></div>}
      {result.next_questions.length > 0 && result.phase !== "evidence_confirmation" && (
        <section className="question-band"><strong>还需要补充</strong>{result.next_questions.map((item) => <span key={item}>{item}</span>)}</section>
      )}

      <div className="workspace-grid">
        <div className="artifact-column">
          {stage === "context" && (
            <ProductImagePanel taskId={result.task_id} state={state} busy={replaceImageMutation.isPending} error={replaceImageMutation.error} onReplace={(image) => replaceImageMutation.mutate(image)} />
          )}
          {stage === "copy" && (
            <>
              <DraftPanel state={state} currentRecord={currentRecord} activeRequirements={activeRequirements} />
              <SourceImageDetails taskId={result.task_id} state={state} busy={replaceImageMutation.isPending} error={replaceImageMutation.error} onReplace={(image) => replaceImageMutation.mutate(image)} />
            </>
          )}
          {stage === "image" && promotionImage && (
            <>
              <PromotionImagePanel taskId={result.task_id} image={promotionImage} />
              <DraftDetails state={state} />
              <SourceImageDetails taskId={result.task_id} state={state} busy={replaceImageMutation.isPending} error={replaceImageMutation.error} onReplace={(image) => replaceImageMutation.mutate(image)} />
            </>
          )}
          {stage === "delivery" && state.final_release_package && (
            <>
              <DeliveryPanel taskId={result.task_id} packageData={state.final_release_package} />
              <SourceImageDetails taskId={result.task_id} state={state} busy={replaceImageMutation.isPending} error={replaceImageMutation.error} onReplace={(image) => replaceImageMutation.mutate(image)} />
            </>
          )}

          <RevisionHistory revisions={revisions} currentRevision={currentRevision} busy={continueMutation.isPending} onRestore={restoreRevision} />
          <Conversation items={state.conversation} />
          <section className="collaboration-section">
            <div className="section-heading"><div><span className="eyebrow">继续协作</span><h2>告诉 Agent 需要解释或调整的内容</h2></div></div>
            <form onSubmit={submitMessage}>
              <textarea value={message} onChange={(event) => setMessage(event.target.value)} rows={4} placeholder={collaborationPlaceholder(stage)} />
              {continueMutation.error && <div className="inline-error"><AlertTriangle size={16} />{errorMessage(continueMutation.error)}</div>}
              <div className="form-actions"><span>修改、提问、比较和恢复版本都可以直接说明</span><button className="primary-button" disabled={continueMutation.isPending || !message.trim()}>{continueMutation.isPending ? <><LoaderCircle className="spin" size={17} />Agent 正在处理</> : <><Send size={17} />发送</>}</button></div>
            </form>
          </section>
        </div>

        <StageActionPanel
          stage={stage}
          state={state}
          review={review}
          comparison={state.review_comparison}
          questions={result.next_questions}
          confirmationSelections={confirmationSelections}
          busy={continueMutation.isPending}
          onConfirmationChange={updateConfirmation}
          onAdvance={advanceStage}
        />
      </div>

      {diagnosticsOpen && (
        <>
          <button className="drawer-scrim" aria-label="关闭运行诊断" onClick={() => setDiagnosticsOpen(false)} />
          <aside className="diagnostics-drawer" role="dialog" aria-modal="true" aria-label="运行诊断">
            <div className="drawer-heading"><div><span className="eyebrow">开发排查</span><h2>运行诊断</h2></div><button className="icon-button" title="关闭运行诊断" onClick={() => setDiagnosticsOpen(false)}><X size={18} /></button></div>
            <DiagnosticsPanel result={result} />
          </aside>
        </>
      )}
    </div>
  );
}

function workspaceStage(phase: string): WorkspaceStage {
  if (phase === "release_package_ready") return "delivery";
  if (phase.startsWith("promotion_image_")) return "image";
  if (phase === "collect_context" || phase.startsWith("source_image_")) return "context";
  return "copy";
}

const taskStages: Array<{ id: WorkspaceStage; label: string }> = [
  { id: "context", label: "信息整理" },
  { id: "copy", label: "文案确认" },
  { id: "image", label: "宣传图确认" },
  { id: "delivery", label: "发布包" },
];

function TaskProgress({ currentStage }: { currentStage: WorkspaceStage }) {
  const currentIndex = taskStages.findIndex((item) => item.id === currentStage);
  return <ol className="task-progress" aria-label="任务进度">{taskStages.map((item, index) => <li key={item.id} className={index < currentIndex ? "complete" : index === currentIndex ? "current" : "upcoming"}><span>{index < currentIndex ? <Check size={14} /> : index + 1}</span><strong>{item.label}</strong>{index < taskStages.length - 1 && <ArrowRight size={15} />}</li>)}</ol>;
}

function ProductImagePanel({ taskId, state, busy, error, onReplace, embedded = false }: {
  taskId: string;
  state: ReleaseTaskResult["state"];
  busy: boolean;
  error: unknown;
  onReplace: (image: File) => void;
  embedded?: boolean;
}) {
  const content = (
    <>
      <div className="section-heading">
        <div><span className="eyebrow">当前成果</span><h2>商品信息与实物图</h2></div>
        <label className={`icon-text-button image-replace ${busy ? "disabled" : ""}`} title="更换商品实物图">
          {busy ? <LoaderCircle className="spin" size={15} /> : <ImageUp size={15} />}
          更换实物图
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            disabled={busy}
            onChange={(event) => {
              const image = event.target.files?.[0];
              if (image) onReplace(image);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      <div className="image-grid">
        <figure>
          <img src={releaseApi.assetUrl(taskId, state.source_image_asset_id)} alt="商品实物图" />
          <figcaption>商品实物图</figcaption>
        </figure>
      </div>
      {Boolean(error) && <div className="inline-error"><AlertTriangle size={16} />{errorMessage(error)}</div>}
    </>
  );
  return embedded ? <div className="supporting-content">{content}</div> : <section className="section-block image-section">{content}</section>;
}

function SourceImageDetails(props: Parameters<typeof ProductImagePanel>[0]) {
  return <details className="section-block supporting-artifact"><summary><span><ImageIcon size={17} /><strong>商品实物图</strong><small>查看或更换商品依据</small></span><ChevronDown size={16} /></summary><ProductImagePanel {...props} embedded /></details>;
}

function DraftPanel({ state, currentRecord, activeRequirements }: { state: ReleaseTaskResult["state"]; currentRecord: Revision | undefined; activeRequirements: ReleaseTaskResult["state"]["active_requirements"] }) {
  const [copied, setCopied] = useState(false);
  return <section className="section-block draft-section"><div className="section-heading"><div><span className="eyebrow">当前成果</span><h2>{currentRecord?.status === "candidate" ? "候选文案，尚未生效" : `文案版本 v${state.current_revision}`}</h2></div><button className="icon-button" title="复制当前文案" disabled={!state.current_draft} onClick={async () => { await navigator.clipboard.writeText(state.current_draft); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>{copied ? <Check size={17} /> : <Copy size={17} />}</button></div>{state.current_draft ? <pre className="draft-copy">{state.current_draft}</pre> : <div className="empty-content">Agent 还没有形成工作稿。</div>}{activeRequirements.length > 0 && <div className="requirements"><span>当前要求</span>{activeRequirements.slice(0, 8).map((item) => <em key={item.requirement_id}>{item.text}</em>)}</div>}</section>;
}

function DraftDetails({ state }: { state: ReleaseTaskResult["state"] }) {
  return <details className="section-block supporting-artifact"><summary><span><FileText size={17} /><strong>已确认文案</strong><small>版本 v{state.current_revision}</small></span><ChevronDown size={16} /></summary><pre className="supporting-copy">{state.current_draft}</pre></details>;
}

function PromotionImagePanel({ taskId, image }: {
  taskId: string;
  image: NonNullable<ReleaseTaskResult["state"]["promotion_image"]>;
}) {
  const statusLabel = {
    awaiting_user: "等待你的确认",
    accepted: "已确认宣传图",
    stale: "宣传图已过期，不会进入发布包",
  }[image.status];
  return (
    <section className="section-block image-section promotion-image-section">
      <div className="section-heading">
        <div>
          <span className="eyebrow">当前成果</span>
          <h2>{statusLabel}</h2>
        </div>
      </div>
      <div className="image-grid">
        <figure>
          <img src={releaseApi.assetUrl(taskId, image.asset_id)} alt="商品宣传图" />
          <figcaption>{image.display_text.join(" · ")}</figcaption>
        </figure>
      </div>
    </section>
  );
}

function RevisionHistory({ revisions, currentRevision, busy, onRestore }: { revisions: Revision[]; currentRevision: number; busy: boolean; onRestore: (revision: number) => void }) {
  if (revisions.length < 2) return null;
  return (
    <details className="section-block revision-history">
      <summary><span><History size={17} /><strong>文案版本</strong><small>{revisions.length} 个版本</small></span><ChevronDown size={16} /></summary>
      <div className="revision-list">{[...revisions].reverse().map((revision) => <article key={revision.revision} className={revision.revision === currentRevision ? "current" : ""}><div><strong>v{revision.revision}</strong><span>{revisionStatusLabel(revision.status)} · {draftOriginLabel(revision.source)}</span></div><p>{revision.content}</p>{revision.revision !== currentRevision && revision.content && <button className="icon-text-button" disabled={busy} onClick={() => onRestore(revision.revision)}><RotateCcw size={14} />恢复此文案</button>}</article>)}</div>
    </details>
  );
}

function Conversation({ items }: { items: ConversationEntry[] }) {
  if (items.length === 0) return null;
  return (
    <section className="section-block conversation">
      <div className="conversation-heading"><span><MessageSquare size={17} /><strong>协作记录</strong><small>{items.length} 条消息</small></span></div>
      <div className="conversation-list">{items.map((item, index) => <div className={item.role === "user" ? "user" : "agent"} key={index}><strong>{item.role === "user" ? "你" : "Agent"}</strong><p>{item.content}</p></div>)}</div>
    </section>
  );
}

function DeliveryPanel({ taskId, packageData }: { taskId: string; packageData: ReleasePackage }) {
  const [copied, setCopied] = useState(false);
  const platformContent = packageData.platform_content;
  const finalCopy = platformContent.body || packageData.final_copy;
  const copyText = [platformContent.title, finalCopy].filter(Boolean).join("\n\n");
  const promotionImageUrl = releaseApi.assetUrl(taskId, packageData.promotion_image_asset_id);
  return (
    <section className="section-block delivery-layout">
      <div className="delivery-header"><div><span className="eyebrow">最终发布包</span><h1>{packageData.product_name || "发布内容"}</h1></div><div className="delivery-score"><ShieldCheck size={18} /><strong>{conclusionLabels[packageData.risk_status] || packageData.risk_status}</strong><span>{packageData.readiness_score}</span></div></div>
      {packageData.review_summary && <p className="delivery-summary">{packageData.review_summary}</p>}
      <div className="delivery-content">
        <div className="delivery-section-heading">
          <strong>发布文案</strong>
          <button className="secondary-button" disabled={!copyText} onClick={async () => { await navigator.clipboard.writeText(copyText); setCopied(true); setTimeout(() => setCopied(false), 1400); }}>
            {copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "已复制" : "复制文案"}
          </button>
        </div>
        {platformContent.title && <div><label>发布标题</label><pre>{platformContent.title}</pre></div>}
        {finalCopy && <div><label>发布正文</label><pre>{finalCopy}</pre></div>}
        {platformContent.script && <div><label>口播参考</label><pre>{platformContent.script}</pre></div>}
      </div>
      <div className="delivery-content">
        <div className="delivery-section-heading">
          <strong>最终宣传图</strong>
          <a className="secondary-button" href={promotionImageUrl} download={`${packageData.product_name || "商品"}-宣传图.png`}><Download size={15} />下载宣传图</a>
        </div>
        <img className="delivery-image" src={promotionImageUrl} alt="最终商品宣传图" />
      </div>
      {packageData.pending_items.length > 0 && <div className="pending-items"><AlertTriangle size={17} /><div><strong>发布前待核对</strong>{packageData.pending_items.map((item) => <span key={item}>{item}</span>)}</div></div>}
      {packageData.publish_checklist.length > 0 && <div className="checklist"><h2>发布前检查</h2>{packageData.publish_checklist.map((item) => <div key={item}><Check size={15} />{item}</div>)}</div>}
    </section>
  );
}

function DiagnosticsPanel({ result }: { result: ReleaseTaskResult }) {
  return (
    <div className="diagnostics">
      <dl><div><dt>任务 ID</dt><dd>{result.task_id}</dd></div><div><dt>状态版本</dt><dd>{result.state.state_version}</dd></div><div><dt>运行状态</dt><dd>{result.status}</dd></div><div><dt>当前阶段</dt><dd>{result.phase}</dd></div></dl>
      <div className="trace-list">{result.trace_events.length === 0 ? <div className="empty-content">没有可显示的运行记录。</div> : result.trace_events.map((event, index) => <details key={`${event.stage}-${index}`}><summary><span>{index + 1}</span><strong>{event.stage}</strong><p>{event.message}</p><ChevronDown size={15} /></summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>)}</div>
    </div>
  );
}

function collaborationPlaceholder(stage: WorkspaceStage): string {
  if (stage === "image") return "说明希望调整的构图、颜色、文字或视觉风格，也可以直接提问";
  if (stage === "delivery") return "继续修改文案或宣传图，或者询问当前发布包";
  if (stage === "context") return "补充商品、平台、卖点或其他任务信息";
  return "说明希望修改的文案内容、语气或卖点，也可以提问、比较或恢复版本";
}

function draftOriginLabel(value: string): string {
  return ({ user_provided: "用户草稿", agent_generated: "Agent 生成", agent_revised: "按要求修改", risk_optimized: "风险优化" } as Record<string, string>)[value] || value || "待生成";
}

function revisionStatusLabel(value: string): string {
  return ({ accepted: "已生效", candidate: "候选版本", rejected: "未生效" } as Record<string, string>)[value] || value;
}
