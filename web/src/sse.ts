/**
 * Two SSE hooks, same shape (handoff §6.2): EventSource, parse `data:`,
 * ignore comment lines (heartbeats), pass typed events to the caller's
 * reducer. Auto-reconnect is the browser's (both streams set `retry:`).
 *
 * Trap T7: EventSource handlers see stale React state (closures) — the
 * callback lives in a ref refreshed every render, so handlers always call
 * the latest (reducer dispatches are stable, but derived closures are not).
 *
 * Reconnect state refetch:
 * - hub: the next `hello` IS the refetch — nothing to do here.
 * - chat: events during the gap are lost forever (SSE has no replay, trap
 *   T6) — `onOpen` fires on EVERY successful open (initial + reconnect);
 *   the caller refetches history + health and replaces the transcript
 *   wholesale (history is the truth).
 */

import { useEffect, useRef } from "react";

function openStream(
  url: string,
  onMessage: (data: string) => void,
  onOpen: () => void,
): () => void {
  const source = new EventSource(url);
  source.onopen = onOpen;
  source.onmessage = (ev: MessageEvent<string>) => {
    if (ev.data.startsWith(":")) return; // comment/heartbeat (defensive; the
    // browser never dispatches comments as messages — they're SSE comments)
    onMessage(ev.data);
  };
  return () => source.close();
}

export function useHubEvents(onEvent: (event: import("./api").HubEvent) => void): void {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  useEffect(() => {
    return openStream(
      "/api/events",
      (data) => handlerRef.current(JSON.parse(data) as import("./api").HubEvent),
      () => {}, // hello carries the full state sync — nothing extra needed
    );
  }, []);
}

export function useChatEvents(
  onEvent: (event: import("./api").ChatEvent) => void,
  onOpen: () => void,
): void {
  const handlerRef = useRef(onEvent);
  const openRef = useRef(onOpen);
  handlerRef.current = onEvent;
  openRef.current = onOpen;
  useEffect(() => {
    return openStream(
      "/api/chat/events",
      (data) => handlerRef.current(JSON.parse(data) as import("./api").ChatEvent),
      () => openRef.current(),
    );
  }, []);
}
