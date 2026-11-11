/**
 * ChatPane render tests (handoff §10): busy disables the composer; the
 * down-banner shows the reason. EventSource + fetch are stubbed at the hook
 * seam (jsdom has neither); events are pushed through the fake source to
 * exercise the real hook → reducer → render path.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPane } from "./ChatPane";
import type { ChatInfo } from "../api";

class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }
  close(): void {}
}

const fetchJson = vi.fn(async (url: string) => {
  if (url.endsWith("/api/chat/history")) {
    return { ok: true, json: async () => ({ messages: [{ role: "assistant", text: "old reply" }] }) };
  }
  if (url.endsWith("/api/chat/health")) {
    return { ok: true, json: async () => ({ ok: true, busy: false, model: "zai/glm-5.3", session_file: null }) };
  }
  throw new Error(`unexpected fetch: ${url}`);
});

beforeEach(() => {
  (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource;
  vi.stubGlobal("fetch", fetchJson);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  FakeEventSource.last = null;
});

const ready: ChatInfo = { state: "ready", model: "zai/glm-5.3" };
const down: ChatInfo = { state: "down", reason: "sidecar exited before ready (exit=1): boom" };

function emit(payload: unknown): void {
  const source = FakeEventSource.last;
  if (source === null) throw new Error("no EventSource created");
  act(() => {
    source.onmessage?.({ data: JSON.stringify(payload) });
  });
}

describe("ChatPane", () => {
  it("loads history on open and streams a live bubble", async () => {
    render(<ChatPane chatInfo={ready} />);
    // resync fires on open: history first (§6.3)
    await act(async () => {
      FakeEventSource.last?.onopen?.();
      await Promise.resolve();
    });
    emit({ type: "agent_start" });
    emit({ type: "text_delta", delta: "writing plot.py…" });
    expect(screen.getByText("writing plot.py…")).toBeTruthy();
  });

  it("agent_start disables the composer (one in-flight turn) and shows Stop", () => {
    render(<ChatPane chatInfo={ready} />);
    const input = screen.getByPlaceholderText(/Describe the plot/);
    expect((input as HTMLTextAreaElement).disabled).toBe(false);
    emit({ type: "agent_start" });
    // composer is replaced by the Stop button while running
    expect(screen.queryByPlaceholderText(/Describe the plot/)).toBeNull();
    expect(screen.getByText("■ Stop")).toBeTruthy();
    emit({ type: "stopped" });
    expect(screen.getByPlaceholderText(/Describe the plot/)).toBeTruthy();
  });

  it("New chat is disabled while running, enabled when idle", () => {
    render(<ChatPane chatInfo={ready} />);
    const newBtn = () => screen.getByText("New chat") as HTMLButtonElement;
    expect(newBtn().disabled).toBe(false);
    emit({ type: "agent_start" });
    expect(newBtn().disabled).toBe(true);
    emit({ type: "agent_end" });
    expect(newBtn().disabled).toBe(false);
  });

  it("down sidecar → banner with the reason, composer disabled", () => {
    render(<ChatPane chatInfo={down} />);
    const banner = screen.getByText(/chat unavailable \(down\)/);
    expect(banner.textContent).toContain("boom");
    const input = screen.getByPlaceholderText(/chat unavailable/);
    expect((input as HTMLTextAreaElement).disabled).toBe(true);
  });

  it("shows the model id in the pane header", () => {
    render(<ChatPane chatInfo={ready} />);
    expect(screen.getByText("zai/glm-5.3")).toBeTruthy();
  });
});
