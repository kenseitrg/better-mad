/**
 * ALL endpoint types + fetch helpers — the single source of truth for the
 * hub API (handoff M3.4 §4; the catalog is descriptive, hub/app.py is
 * normative). Chat payloads are the sidecar's ClientEvent union (M3.3 §4),
 * proxied verbatim by the hub.
 */

// --- health / status ---------------------------------------------------------

export type LifecycleState = "starting" | "ready" | "restarting" | "down";

export interface WorkerInfo {
  state: LifecycleState;
  port: number | null;
  epoch: number;
  restarts: number;
}

/** Hub view of the chat sidecar; `"disabled"` when the hub runs sidecar-less. */
export interface ChatInfo {
  state: LifecycleState | "disabled";
  port?: number | null;
  model?: string | null;
  restarts?: number;
  reason?: string;
}

export interface Health {
  ok: boolean;
  workspace: string;
  worker: WorkerInfo;
  chat: ChatInfo;
}

export type RunStatus = "ok" | "error" | "timeout" | "busy";

export interface RunResult {
  status: RunStatus;
  run_seconds: number;
  stdout_tail: string;
  stderr_tail: string;
  epoch: number;
}

export interface Status {
  auto_run: boolean;
  last_run: RunResult | null;
  worker: WorkerInfo;
  watcher: { last_change_ts: number | null; pending: boolean };
  chat: ChatInfo;
}

// --- embed -------------------------------------------------------------------

export interface EmbedInfo {
  /** `http://127.0.0.1:<port>/bm`, or `null` while no worker port is known. */
  server_url: string | null;
  session_id: string;
  epoch: number;
}

// --- files -------------------------------------------------------------------

export interface ColumnInfo {
  original: string;
  script: string;
  dtype: string;
  min: number | null;
  max: number | null;
  nan_pct: number;
}

export interface DatasetSummary {
  name: string;
  source: string;
  rows: number;
  columns: ColumnInfo[];
}

export interface FilesInfo {
  datasets: DatasetSummary[];
}

// --- chat (sidecar contract, proxied verbatim) --------------------------------

export interface ChatHealth {
  ok: boolean;
  busy: boolean;
  model: string | null;
  session_file: string | null;
}

export interface ChatHistoryMessage {
  role: "user" | "assistant";
  text: string;
}

export type ChatEvent =
  | { type: "text_delta"; delta: string }
  | { type: "agent_start" }
  | { type: "agent_end" }
  | { type: "tool_execution_start"; toolName: string }
  | { type: "tool_execution_end"; toolName: string; isError: boolean }
  | { type: "stopped" }
  | { type: "reset" }
  | { type: "error"; message: string };

// --- hub SSE (GET /api/events) --------------------------------------------------

export type HubEvent =
  | ({ type: "hello" } & Status)
  | { type: "plot_changed" }
  | ({ type: "run_result" } & RunResult)
  | ({ type: "worker" } & WorkerInfo)
  | ({ type: "chat" } & ChatInfo);

// --- fetch helpers ----------------------------------------------------------------
// Plain fetch-and-render discipline (pwb): no query library, no caching layer.

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

async function postJson<T>(url: string, payload: unknown, method = "POST"): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export const api = {
  health: () => getJson<Health>("/api/health"),
  status: () => getJson<Status>("/api/status"),

  files: () => getJson<FilesInfo>("/api/files"),
  /** Import one file with default parse settings (settings UI is M3.5). */
  importFile: async (file: File): Promise<DatasetSummary> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/files/import", { method: "POST", body: form });
    const body: unknown = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, body);
    return body as DatasetSummary;
  },

  getPlot: () => getJson<{ content: string }>("/api/plot.py"),
  /** Atomic full-file write; unchanged content is a server-side no-op. */
  putPlot: (content: string) => postJson<{ ok: boolean }>("/api/plot.py", { content }, "PUT"),

  run: (timeout_s?: number) =>
    postJson<RunResult>("/api/run", timeout_s !== undefined ? { timeout_s } : {}),
  autorun: (on: boolean) => postJson<{ auto_run: boolean }>("/api/autorun", { on }),
  embed: () => getJson<EmbedInfo>("/api/plot/embed"),

  chatHealth: () => getJson<ChatHealth>("/api/chat/health"),
  chatHistory: () => getJson<{ messages: ChatHistoryMessage[] }>("/api/chat/history"),
  /** 202 {started} | 400 | 409 (busy) — 409 is an expected flow, not an error. */
  chatSend: (text: string) => postJson<{ started?: boolean }>("/api/chat/send", { text }),
  /** 202 {stopping} | 409 (idle — fine, the turn already ended). */
  chatStop: () => postJson<{ stopping?: boolean }>("/api/chat/stop", {}),
  /** 202 {reset} | 409 (busy). The SSE `reset` event drives the UI. */
  chatNew: () => postJson<{ reset?: boolean }>("/api/chat/new", {}),
};
