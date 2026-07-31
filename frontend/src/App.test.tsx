import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ReleaseTaskResult, ReleaseTaskSummary } from "./types";

const summary: ReleaseTaskSummary = {
  task_id: "task-1",
  status: "waiting_user",
  phase: "draft_review_ready",
  product_name: "测试耳机",
  product_category: "数码家电",
  platform: "xiaohongshu",
  current_revision: 1,
  updated_at: "2026-07-21T08:00:00Z",
};

function taskResult(
  taskId = "task-1",
  productName = "测试耳机",
  draft = "待发布的耳机文案。",
): ReleaseTaskResult {
  const review = {
    revision: 1,
    content: draft,
    publication_conclusion: "safe_to_publish" as const,
    publication_action: "allow" as const,
    review_outcome: "safe" as const,
    readiness_score: 100,
    summary: "当前文案可以发布。",
    decisions: [],
    unfulfilled_requirement_ids: [],
    human_confirmation_items: [],
  };
  return {
    task_id: taskId,
    status: "waiting_user",
    phase: "draft_review_ready",
    answer: "当前工作稿已完成审核。",
    next_questions: [],
    state: {
      schema_version: 13,
      task_id: taskId,
      task_brief: "测试任务",
      product_name: productName,
      product_category: "数码家电",
      platform: "xiaohongshu",
      objective: "生成发布文案",
      source_image_asset_id: "source-image-1",
      image_analysis: {},
      promotion_image: null,
      requirements: [],
      active_requirements: [],
      current_revision: 1,
      current_draft: draft,
      draft_origin: "agent_generated",
      current_review: review,
      review_comparison: null,
      pending_confirmation: null,
      confirmed_evidence: [],
      revisions: [{
        revision: 1,
        content: draft,
        source: "agent_generated",
        instruction: "",
        status: "accepted",
        review,
      }],
      conversation: [],
      events: [],
      final_release_package: null,
      last_turn_error: null,
      state_version: 1,
      next_requirement_number: 1,
    },
    trace_events: [],
  };
}

function response(value: unknown, status = 200): Response {
  return status === 204
    ? new Response(null, { status })
    : Response.json(value, { status });
}

function renderApp(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("release task workspace", () => {
  it("creates a task from natural-language requirements", async () => {
    const created = taskResult();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/release-tasks" && init?.method === "POST") return response(created);
      if (path === "/release-tasks") return response([]);
      throw new Error(`Unexpected request: ${path}`);
    });
    renderApp(fetchMock);

    fireEvent.change(await screen.findByLabelText("任务要求"), {
      target: { value: "在小红书宣传一款测试耳机" },
    });
    const productImage = new File(["image"], "product.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText(/选择商品实物图/), {
      target: { files: [productImage] },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建并生成工作稿" }));

    expect(await screen.findByText("待发布的耳机文案。")).toBeInTheDocument();
    const createCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    const body = createCall?.[1]?.body as FormData;
    expect(body.get("task_brief")).toBe("在小红书宣传一款测试耳机");
    expect(body.get("product_image")).toBe(productImage);
    expect(new Headers(createCall?.[1]?.headers).has("Content-Type")).toBe(false);
  });

  it("switches between persisted tasks", async () => {
    const secondSummary = { ...summary, task_id: "task-2", product_name: "第二商品" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/release-tasks") return response([summary, secondSummary]);
      if (path === "/release-tasks/task-1") return response(taskResult());
      if (path === "/release-tasks/task-2") {
        return response(taskResult("task-2", "第二商品", "第二个任务的文案。"));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    renderApp(fetchMock);

    expect(await screen.findByText("待发布的耳机文案。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "第二商品 小红书 · v1 工作稿待确认" }));

    expect(await screen.findByText("第二个任务的文案。")).toBeInTheDocument();
  });

  it("uses stage-aware actions and keeps diagnostics out of the main workspace", async () => {
    const current = taskResult();
    current.state.conversation = [
      { role: "user", content: "标题短一点", phase: "draft_review_ready", status: "waiting_user", decision: "" },
      { role: "assistant", content: "已缩短标题。", phase: "draft_review_ready", status: "waiting_user", decision: "" },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/release-tasks/task-1") return response(current);
      return response([summary]);
    });
    renderApp(fetchMock);

    expect(await screen.findByRole("list", { name: "任务进度" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "审核并确认文案" })).toBeInTheDocument();
    expect(screen.getByText("标题短一点")).toBeVisible();
    expect(screen.getByText("已缩短标题。")).toBeVisible();
    expect(screen.queryByText("内容协作")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "运行诊断" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle("运行诊断"));
    expect(screen.getByRole("dialog", { name: "运行诊断" })).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("关闭运行诊断"));
    expect(screen.queryByRole("dialog", { name: "运行诊断" })).not.toBeInTheDocument();
  });

  it("requires explicit confirmation before permanently deleting a task", async () => {
    let deleted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "DELETE") {
        deleted = true;
        return response(null, 204);
      }
      if (path === "/release-tasks/task-1") return response(taskResult());
      return response(deleted ? [] : [summary]);
    });
    renderApp(fetchMock);

    expect(await screen.findByText("待发布的耳机文案。")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("删除任务"));
    expect(screen.getByRole("dialog", { name: "永久删除任务？" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
    });
  });

  it("submits one independent resolution for every pending confirmation", async () => {
    const pending = taskResult();
    const confirmationItems = [
      {
        decision_id: "decision-1-1",
        origin: "requirement" as const,
        requirement_id: "req-1",
        matched_text: "宣传事实A",
        label: "资质声明",
        risk_family: "qualification",
        severity: "high" as const,
        reason: "需要核验宣传事实A。",
        human_confirmation_eligible: true,
        action: "confirm" as const,
      },
      {
        decision_id: "decision-1-2",
        origin: "requirement" as const,
        requirement_id: "req-2",
        matched_text: "宣传事实B",
        label: "促销条件",
        risk_family: "conditional_promotion",
        severity: "medium" as const,
        reason: "需要核验宣传事实B。",
        human_confirmation_eligible: true,
        action: "confirm" as const,
      },
    ];
    pending.phase = "evidence_confirmation";
    pending.state.pending_confirmation = {
      revision: 1,
      items: confirmationItems,
      review: pending.state.current_review!,
    };
    pending.state.current_review!.publication_conclusion = "revise_before_publish";
    pending.state.current_review!.decisions = confirmationItems;
    pending.state.current_review!.human_confirmation_items = confirmationItems;
    pending.state.revisions![0].status = "candidate";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/continue") && init?.method === "POST") return response(taskResult());
      if (path === "/release-tasks/task-1") return response(pending);
      return response([summary]);
    });
    renderApp(fetchMock);

    const submit = await screen.findByRole("button", { name: "提交逐项决定" });
    expect(submit).toBeDisabled();
    const confirmChoices = screen.getAllByDisplayValue("confirmed_with_basis");
    const rewriteChoices = screen.getAllByDisplayValue("rewrite_without_basis");
    fireEvent.click(confirmChoices[0]);
    fireEvent.change(screen.getByLabelText("宣传事实A的核验说明"), {
      target: { value: "材料可核验" },
    });
    expect(submit).toBeDisabled();
    fireEvent.click(rewriteChoices[1]);
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => {
      const continueCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      const body = JSON.parse(String(continueCall?.[1]?.body));
      expect(body.confirmation_resolutions).toEqual([
        {
          decision_id: "decision-1-1",
          resolution: "confirmed_with_basis",
          evidence_notes: "材料可核验",
        },
        {
          decision_id: "decision-1-2",
          resolution: "rewrite_without_basis",
          evidence_notes: "",
        },
      ]);
      expect(body.expected_state_version).toBe(1);
    });
  });

  it("generates and confirms a promotion image before finalizing", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const completed = taskResult();
    const ready = taskResult();
    const imageReady = taskResult();
    imageReady.phase = "promotion_image_review_ready";
    imageReady.state.promotion_image = {
      asset_id: "promotion-image-1",
      display_text: ["轻盈聆听", "到手99元"],
      prompt: "自主设计商品宣传图。",
      instruction: "",
      copy_revision: 1,
      status: "awaiting_user",
    };
    completed.status = "completed";
    completed.phase = "release_package_ready";
    completed.state.final_release_package = {
      product_name: "测试耳机",
      platform: "xiaohongshu",
      risk_status: "safe_to_publish",
      readiness_score: 100,
      package_status: "ready_to_publish",
      product_category: "数码家电",
      revision: 1,
      review_summary: "当前文案可以发布。",
      final_copy: "最终发布文案。",
      promotion_image_asset_id: "promotion-image-1",
      promotion_image_text: ["轻盈聆听", "到手99元"],
      platform_content: {
        platform: "xiaohongshu",
        title: "",
        body: "最终发布文案。",
        script: "",
      },
      requirement_delivery: [],
      review_decisions: [],
      confirmed_evidence: [],
      pending_items: [],
      publish_checklist: [],
    };
    let continueCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/continue") && init?.method === "POST") {
        continueCount += 1;
        return response(continueCount === 1 ? imageReady : completed);
      }
      if (path === "/release-tasks/task-1") return response(ready);
      return response([summary]);
    });
    renderApp(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "确认文案并生成宣传图" }));
    expect(await screen.findByRole("heading", { name: "等待你的确认" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认宣传图并生成发布包" }));

    expect(await screen.findByText("最终发布文案。")).toBeInTheDocument();
    expect(screen.getByAltText("最终商品宣传图")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "复制文案" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("最终发布文案。"));
    const downloadLink = screen.getByRole("link", { name: "下载宣传图" });
    expect(downloadLink).toHaveAttribute("href", "/release-tasks/task-1/assets/promotion-image-1");
    expect(downloadLink).toHaveAttribute("download", "测试耳机-宣传图.png");
    const continueCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(continueCalls).toHaveLength(2);
    expect(JSON.parse(String(continueCalls[0]?.[1]?.body)).message).toBe("");
    expect(JSON.parse(String(continueCalls[1]?.[1]?.body)).confirmation_resolutions).toEqual([]);
  });

  it("marks an outdated promotion image as excluded from delivery", async () => {
    const stale = taskResult();
    stale.phase = "promotion_image_revision_needed";
    stale.state.promotion_image = {
      asset_id: "promotion-image-old",
      display_text: ["旧版卖点"],
      prompt: "旧版宣传图",
      instruction: "",
      copy_revision: 1,
      status: "stale",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/release-tasks/task-1") return response(stale);
      return response([summary]);
    });

    renderApp(fetchMock);

    expect(await screen.findByText("宣传图已过期，不会进入发布包")).toBeInTheDocument();
    expect(screen.queryByText("已确认宣传图")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成新宣传图" })).toBeInTheDocument();
  });
});
