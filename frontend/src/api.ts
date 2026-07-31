import type {
  ContinueTaskInput,
  ReleaseTaskResult,
  ReleaseTaskSummary,
} from "./types";

interface ErrorEnvelope {
  detail?: { code?: string; message?: string } | string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    headers,
  });
  if (response.ok) {
    return response.status === 204 ? (undefined as T) : response.json();
  }
  let payload: ErrorEnvelope = {};
  try {
    payload = await response.json();
  } catch {
    // The HTTP status remains useful when an upstream proxy returns plain text.
  }
  const detail = typeof payload.detail === "object" ? payload.detail : undefined;
  throw new ApiError(
    response.status,
    detail?.code ?? "api_error",
    detail?.message ?? `请求失败（HTTP ${response.status}）`,
  );
}

export const releaseApi = {
  list: () => request<ReleaseTaskSummary[]>("/release-tasks"),
  get: (taskId: string) => request<ReleaseTaskResult>(`/release-tasks/${taskId}`),
  create: (taskBrief: string, productImage: File) => {
    const body = new FormData();
    body.set("task_brief", taskBrief);
    body.set("product_image", productImage);
    return request<ReleaseTaskResult>("/release-tasks", { method: "POST", body });
  },
  continue: (taskId: string, input: ContinueTaskInput) =>
    request<ReleaseTaskResult>(`/release-tasks/${taskId}/continue`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  replaceImage: (taskId: string, productImage: File, expectedStateVersion: number) => {
    const body = new FormData();
    body.set("product_image", productImage);
    body.set("expected_state_version", String(expectedStateVersion));
    return request<ReleaseTaskResult>(`/release-tasks/${taskId}/product-image`, {
      method: "POST",
      body,
    });
  },
  delete: (taskId: string) =>
    request<void>(`/release-tasks/${taskId}`, { method: "DELETE" }),
  assetUrl: (taskId: string, assetId: string) =>
    `/release-tasks/${encodeURIComponent(taskId)}/assets/${encodeURIComponent(assetId)}`,
};
