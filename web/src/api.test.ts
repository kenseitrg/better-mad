/**
 * api.ts contract tests (handoff §10): REAL payloads captured from a live
 * hub (M3.2/M3.3 verified stack, workspace qa-m34, 2026-11-11). Typed
 * assignments double as compile-time contract checks; the runtime asserts
 * pin the load-bearing fields.
 */
import { describe, expect, it } from "vitest";
import type {
  ChatHealth,
  EmbedInfo,
  FilesInfo,
  Health,
  HubEvent,
  Status,
} from "./api";

// --- fixtures: captured verbatim from the live hub ---------------------------

const healthFixture = {
  ok: true,
  workspace: "/home/am/.cache/better-mad/workspaces/qa-m34",
  worker: { state: "ready", port: 41873, epoch: 1, restarts: 0 },
  chat: { state: "ready", port: 35613, model: "zai/glm-5.3", restarts: 0 },
};

const statusFixture = {
  auto_run: true,
  last_run: {
    status: "ok",
    run_seconds: 0.9438432609895244,
    stdout_tail: "rows: 708650\n",
    stderr_tail: "",
    epoch: 1,
  },
  worker: { state: "ready", port: 41873, epoch: 1, restarts: 0 },
  watcher: { last_change_ts: 1794386621.6480699, pending: false },
  chat: { state: "ready", port: 35613, model: "zai/glm-5.3", restarts: 0 },
};

const filesFixture = {
  datasets: [
    {
      name: "14_01_post_stack_attr_after_scac_all",
      source: "data/14_01_post_stack_attr_after_scac_all",
      rows: 708650,
      columns: [
        {
          original: "CMP",
          script: "CMP",
          dtype: "float32",
          min: 876149.0,
          max: 1640334.0,
          nan_pct: 0.0,
        },
        {
          original: "3DT_SEC_ORD_CELCTR",
          script: "X3DT_SEC_ORD_CELCTR", // leading-digit sanitization (design §6)
          dtype: "float32",
          min: 1002.0,
          max: 1775.0,
          nan_pct: 0.0,
        },
        {
          original: "TR.DOMFREQ",
          script: "TR_DOMFREQ", // dot sanitization
          dtype: "float32",
          min: 0.0,
          max: 56.766998291015625,
          nan_pct: 0.0,
        },
      ],
    },
  ],
};

const embedFixture = {
  server_url: "http://127.0.0.1:41873/bm",
  session_id: "bm-main",
  epoch: 1,
};

const chatHealthFixture = {
  ok: true,
  busy: false,
  model: "zai/glm-5.3",
  session_file:
    "/home/am/.cache/better-mad/workspaces/qa-m34/.chat/2026-11-11T08-42-27-362Z_01a1.jsonl",
};

// --- contract tests -----------------------------------------------------------

describe("api payload contracts (live-hub fixtures)", () => {
  it("GET /api/health parses as Health", () => {
    const h = healthFixture as Health;
    expect(h.ok).toBe(true);
    expect(h.worker.state).toBe("ready");
    expect(h.chat.model).toBe("zai/glm-5.3");
  });

  it("GET /api/status parses as Status", () => {
    const s = statusFixture as Status;
    expect(s.auto_run).toBe(true);
    expect(s.last_run?.status).toBe("ok");
    expect(s.watcher.pending).toBe(false);
  });

  it("GET /api/files parses as FilesInfo (sanitized script names present)", () => {
    const f = filesFixture as FilesInfo;
    const columns = f.datasets[0]?.columns ?? [];
    expect(f.datasets[0]?.rows).toBe(708650);
    expect(columns.map((c) => c.script)).toEqual([
      "CMP",
      "X3DT_SEC_ORD_CELCTR",
      "TR_DOMFREQ",
    ]);
  });

  it("GET /api/plot/embed parses as EmbedInfo", () => {
    const e = embedFixture as EmbedInfo;
    expect(e.server_url).toBe("http://127.0.0.1:41873/bm");
    expect(e.session_id).toBe("bm-main");
  });

  it("GET /api/chat/health parses as ChatHealth (the §7.1 proxy)", () => {
    const c = chatHealthFixture as ChatHealth;
    expect(c.ok).toBe(true);
    expect(c.busy).toBe(false);
    expect(typeof c.session_file).toBe("string");
  });

  it("hub SSE hello carries the full status (authoritative resync)", () => {
    const hello: HubEvent = { type: "hello", ...(statusFixture as Status) };
    expect(hello.type).toBe("hello");
    if (hello.type === "hello") expect(hello.last_run?.epoch).toBe(1);
  });

  it("run_result event parses as a run_result HubEvent", () => {
    const event: HubEvent = {
      type: "run_result",
      ...(statusFixture.last_run as NonNullable<Status["last_run"]>),
    };
    expect(event.type).toBe("run_result");
  });
});
