import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  FileText,
  Image as ImageIcon,
  LoaderCircle,
  Menu,
  PackageCheck,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { releaseApi } from "./api";
import {
  errorMessage,
  phaseLabels,
  phaseTone,
  platformLabels,
} from "./releasePresentation";
import { TaskWorkspace } from "./task-workspace/TaskWorkspace";
import type {
  ReleaseTaskResult,
  ReleaseTaskSummary,
} from "./types";

export default function App() {
  const queryClient = useQueryClient();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ReleaseTaskSummary | null>(null);

  const tasksQuery = useQuery({ queryKey: ["release-tasks"], queryFn: releaseApi.list });
  const tasks = tasksQuery.data ?? [];

  useEffect(() => {
    if (!selectedTaskId && !creating && tasks.length > 0) setSelectedTaskId(tasks[0].task_id);
  }, [creating, selectedTaskId, tasks]);

  const taskQuery = useQuery({
    queryKey: ["release-task", selectedTaskId],
    queryFn: () => releaseApi.get(selectedTaskId!),
    enabled: Boolean(selectedTaskId) && !creating,
  });

  const deleteMutation = useMutation({
    mutationFn: releaseApi.delete,
    onSuccess: async (_, deletedId) => {
      queryClient.removeQueries({ queryKey: ["release-task", deletedId] });
      const remaining = tasks.filter((item) => item.task_id !== deletedId);
      setSelectedTaskId(remaining[0]?.task_id ?? null);
      setCreating(remaining.length === 0);
      setDeleteTarget(null);
      await queryClient.invalidateQueries({ queryKey: ["release-tasks"] });
    },
  });

  function selectTask(taskId: string) {
    setSelectedTaskId(taskId);
    setCreating(false);
    setSidebarOpen(false);
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark"><FileText size={18} /></div>
          <div><strong>发布内容 Agent</strong><span>营销内容工作台</span></div>
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} title="关闭导航"><X size={18} /></button>
        </div>
        <button className="primary-button new-task" onClick={() => { setCreating(true); setSelectedTaskId(null); setSidebarOpen(false); }}>
          <Plus size={17} />新建发布任务
        </button>
        <div className="sidebar-section-title">
          <span>任务历史</span>
          <button className="icon-button" onClick={() => tasksQuery.refetch()} title="刷新任务列表"><RefreshCw size={15} /></button>
        </div>
        <nav className="task-list" aria-label="任务历史">
          {tasksQuery.isLoading && <div className="sidebar-empty"><LoaderCircle className="spin" size={18} />正在读取任务</div>}
          {!tasksQuery.isLoading && tasks.length === 0 && <div className="sidebar-empty">还没有发布任务</div>}
          {tasks.map((task) => (
            <div className={`task-list-row ${task.task_id === selectedTaskId && !creating ? "active" : ""}`} key={task.task_id}>
              <button className="task-list-select" onClick={() => selectTask(task.task_id)}>
                <span className="task-list-name">{task.product_name || task.product_category || "未命名发布任务"}</span>
                <span className="task-list-meta">{platformLabels[task.platform] || task.platform || "平台待确认"} · v{task.current_revision}</span>
                <span className="task-list-phase">{phaseLabels[task.phase] || task.phase}</span>
              </button>
              <button className="icon-button delete-task" onClick={() => setDeleteTarget(task)} title="删除任务"><Trash2 size={15} /></button>
            </div>
          ))}
        </nav>
        <div className="sidebar-footer"><span className="status-dot" />单机服务</div>
      </aside>

      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} title="打开导航"><Menu size={19} /></button>
          <div className="topbar-title">{creating ? "创建发布任务" : taskQuery.data?.state.product_name || "发布任务"}</div>
          {taskQuery.data && <span className={`status-badge ${phaseTone(taskQuery.data.phase, taskQuery.data.status)}`}>{phaseLabels[taskQuery.data.phase] || taskQuery.data.phase}</span>}
        </header>

        {creating || (!selectedTaskId && !tasksQuery.isLoading) ? (
          <CreateTask
            onCreated={(result) => {
              queryClient.setQueryData(["release-task", result.task_id], result);
              queryClient.invalidateQueries({ queryKey: ["release-tasks"] });
              setSelectedTaskId(result.task_id);
              setCreating(false);
            }}
          />
        ) : taskQuery.isLoading ? (
          <LoadingState label="正在读取发布任务" />
        ) : taskQuery.error ? (
          <ErrorState message={errorMessage(taskQuery.error)} onRetry={() => taskQuery.refetch()} />
        ) : taskQuery.data ? (
          <TaskWorkspace result={taskQuery.data} />
        ) : null}
      </main>

      {deleteTarget && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !deleteMutation.isPending && setDeleteTarget(null)}>
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="dialog-icon"><Trash2 size={20} /></div>
            <h2 id="delete-title">永久删除任务？</h2>
            <p>“{deleteTarget.product_name || "未命名发布任务"}”的任务记录、调用日志和运行状态都会被清除，删除后无法恢复。</p>
            {deleteMutation.error && <div className="inline-error">{errorMessage(deleteMutation.error)}</div>}
            <div className="dialog-actions">
              <button className="secondary-button" disabled={deleteMutation.isPending} onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="danger-button" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(deleteTarget.task_id)}>
                {deleteMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}确认删除
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function CreateTask({ onCreated }: { onCreated: (result: ReleaseTaskResult) => void }) {
  const [brief, setBrief] = useState("");
  const [productImage, setProductImage] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const mutation = useMutation({
    mutationFn: ({ taskBrief, image }: { taskBrief: string; image: File }) =>
      releaseApi.create(taskBrief, image),
    onSuccess: onCreated,
  });

  useEffect(() => {
    if (!productImage) {
      setPreviewUrl("");
      return;
    }
    const url = URL.createObjectURL(productImage);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [productImage]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (brief.trim() && productImage) {
      mutation.mutate({ taskBrief: brief.trim(), image: productImage });
    }
  }

  return (
    <div className="create-layout">
      <section className="create-form">
        <span className="eyebrow">新发布任务</span>
        <h1>这次准备宣传什么？</h1>
        <p>说明平台、产品和真实卖点；已有草稿也可以直接放在这里。</p>
        <form onSubmit={submit}>
          <label htmlFor="task-brief">任务要求</label>
          <textarea
            id="task-brief"
            autoFocus
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
            placeholder="例如：我要在小红书宣传一款键盘，美加狮品牌，60键位，无灯标准版到手99元，有黑白两种配色。"
            rows={7}
          />
          <label className="image-upload" htmlFor="product-image">
            <input
              id="product-image"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(event) => setProductImage(event.target.files?.[0] ?? null)}
            />
            {previewUrl ? <img src={previewUrl} alt="待上传商品实物图" /> : <ImageIcon size={24} />}
            <span><strong>{productImage?.name || "选择商品实物图"}</strong><small>JPG、PNG 或 WebP，最大 15MB</small></span>
          </label>
          {mutation.error && <div className="inline-error"><AlertTriangle size={16} />{errorMessage(mutation.error)}</div>}
          <button className="primary-button create-submit" disabled={!brief.trim() || !productImage || mutation.isPending}>
            {mutation.isPending ? <><LoaderCircle className="spin" size={17} />Agent 正在处理</> : <><Send size={17} />创建并生成工作稿</>}
          </button>
        </form>
      </section>
      <aside className="create-context">
        <div><ImageIcon size={18} /><strong>实物图作为商品依据</strong><span>Agent 会识别图片中的商品事实，用于生成和核对文案。</span></div>
        <div><ShieldCheck size={18} /><strong>审核与风险优化</strong><span>明确违规内容自动处理，需要依据的事实交给你确认。</span></div>
        <div><PackageCheck size={18} /><strong>确认后交付</strong><span>满意时直接提交，生成与当前版本一致的发布包。</span></div>
      </aside>
    </div>
  );
}

function LoadingState({ label }: { label: string }) {
  return <div className="center-state"><LoaderCircle className="spin" size={25} /><span>{label}</span></div>;
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="center-state error"><AlertTriangle size={25} /><strong>暂时无法读取任务</strong><span>{message}</span><button className="secondary-button" onClick={onRetry}><RefreshCw size={15} />重试</button></div>;
}
