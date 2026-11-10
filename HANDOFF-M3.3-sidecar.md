# HANDOFF — M3.3: Node chat sidecar (pi SDK AgentSession)

You are implementing milestone step **M3.3**: the **chat sidecar** — a Node/
TypeScript process hosting a pi SDK `AgentSession` behind pwb's chat contract —
plus the hub-side lifecycle manager and `/api/chat/*` proxy, and the `--model`
CLI flag. This file is your single source of instructions. Where it conflicts
with your instincts, this file wins.

M3.1 (worker) and M3.2 (hub) are **done and live-verified** — treat them as
stable dependencies. You add `sidecar/`, `hub/sidecar.py`, and small edits to
`hub/app.py` + `cli.py`.

Decisions already made for you (2026-11-10, do not relitigate):

- **HTTP server: plain `node:http`** — 6 localhost routes, zero deps beyond the
  pi SDK (repo convention: lean deps, hand-rolled SSE like the hub's).
- **Execution: Node native TypeScript** — the hub spawns `node sidecar/src/main.ts`
  directly. Type stripping is default-on since Node 22.18; the **Node floor
  rises from ≥20 to ≥22.18** (20 has been EOL since April 2026). Erasable-only
  TS discipline applies (§9, trap T6).
- **Python manager lives in `hub/sidecar.py`** — mirrors `worker/manager.py`;
  design §9's hub bullet ("worker + sidecar lifecycle") is authoritative. Fix
  the stale core bullet in the same commit (§13).

## 1. Mission

```
SPA (M3.4) ── /api/chat/* ──► HUB (you extend) ── same paths ──► SIDECAR (you create)
                             ├─ SidecarManager: spawn / health / restart-once / down
                             └─ dumb byte-passthrough proxy, no interpretation
                                                              Node + pi SDK AgentSession
                                                              cwd = workspace, builtin file tools,
                                                              sessions in workspace/.chat/
```

- **`sidecar/`** (Node/TS): `AgentSession` host. Built-in file tools **on**
  (they are the agent↔app protocol), context files **on** (workspace `AGENTS.md`
  auto-loads), no extensions/prompt-templates/themes, sessions in
  `workspace/.chat/`, 5-min watchdog on hung turns, `--model provider/model`
  via `ModelRuntime`. **No better-mad logic, no custom tools** (design §9.1).
- **`hub/sidecar.py`**: `SidecarManager` — the sidecar's `worker/manager.py`
  analog: spawn, health-poll, restart-once-then-down, Node-missing/version
  degrade, ready-line handshake.
- **`hub/app.py` edits**: `/api/chat/*` proxy routes (reserved in M3.2), `chat`
  section in `/api/health` + `/api/status`, `chat` events on `/api/events`.
- **`cli.py` edit**: `--model provider/model` flag threaded to the sidecar.

**Exit criteria (from PLAN.md M3.3):** sidecar against a fixture workspace;
curl endpoints; "write a hello plot.py" prompt produces the file; SSE streams
text + tool events.

## 2. Read first (~20 min, in order)

| File | Extract |
|------|---------|
| `AGENTS.md` (repo root) | conventions, architecture rule, gotchas |
| `design.md` §9.1 | the sidecar spec you are implementing (§4 + §10 too) |
| `UX.md` §8 | chat behavior contract (stop/new/busy semantics the SPA will rely on) |
| `PLAN.md` M3.3 + R1 | scope, exit criteria, lifecycle risk |
| `HANDOFF-M3.2-hub.md` §4 | hub API the proxy slots into; W8 (SSE) |
| `src/better_mad/worker/manager.py` | the lifecycle pattern to mirror |
| `src/better_mad/worker/lifecycle.py` | `WORKER_READY` stdout-handshake pattern |
| `~/pwb/server/agent/manager.ts` | **lift** `runTurnWithWatchdog`, busy/stop/reset state machine, event fanout |
| `~/pwb/server/agent/sessions.ts` | **lift** `resolveModel`, session factory, `historyForClient` (ignore persona/skill/custom-tools machinery) |
| `~/pwb/server/routes/chat.ts` | endpoint semantics + `openEventStream` (port to node:http) |
| pi SDK docs: `/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/docs/sdk.md` | session lifecycle, events, config boundaries |

Conventions: Python gates `uv run pytest` / `uv run ruff check .` /
`uv run ty check`; sidecar gates `npm test` + `npm run typecheck` (in
`sidecar/`). All green before committing; commits prefixed `M3.3:`.

## 3. Code map

```
sidecar/                        ← YOU CREATE (Node ≥22.18, native TS)
├── package.json                # name better-mad-sidecar, private, type: module
├── tsconfig.json               # typecheck-only (noEmit) — see §9
├── src/
│   ├── main.ts                 # argv (--workspace, --port, --model), boot, SIGTERM
│   ├── session.ts              # createChatSession(): loader + SessionManager + model
│   ├── manager.ts              # ChatManager: send/stop/new/history + fanout + watchdog
│   └── server.ts               # node:http routing + SSE writer (pwb openEventStream port)
└── test/                       # vitest, all session-stubbed (no network, no model)
src/better_mad/
├── hub/
│   ├── app.py                  ← YOU EDIT: proxy routes, chat status/events, lifespan
│   └── sidecar.py              ← YOU CREATE: SidecarManager
├── worker/                     # DONE — do not touch
└── cli.py                      ← YOU EDIT: --model flag + SidecarManager wiring
```

## 4. Sidecar HTTP contract (localhost only; hub proxies verbatim)

The sidecar serves the **same paths the SPA will call** — the hub proxy is a
dumb same-path passthrough, no rewriting:

| Endpoint | In | Out |
|---|---|---|
| `GET /api/chat/events` | — | SSE; `retry: 2000`, heartbeat `: ping` comment every 25 s; payloads = `ClientEvent` union below |
| `POST /api/chat/send` | `{"text": str}` | `202 {"started": true}`; `400` empty/non-string text; `409 {"error": "already processing a turn"}` |
| `POST /api/chat/stop` | — | `202 {"stopping": true}`; `409` when idle |
| `POST /api/chat/new` | — | `202 {"reset": true}`; `409` while a turn is in flight |
| `GET /api/chat/history` | — | `{"messages": [{"role": "user"\|"assistant", "text": str}]}` |
| `GET /health` | — | `{"ok": true, "busy": bool, "model": "provider/id", "session_file": str\|null}` |

SSE payloads (`ClientEvent` — pwb's union verbatim):

```
{type: "text_delta", delta}
{type: "agent_start"} / {type: "agent_end"}
{type: "tool_execution_start", toolName} / {type: "tool_execution_end", toolName, isError}
{type: "stopped"}        // user stop or watchdog — calm marker, input re-enables
{type: "reset"}          // session rotated — clients clear the transcript
{type: "error", message} // provider failures etc.; busy flag releases
```

Unknown path → `404 {"error": ...}`; malformed JSON → `400`. No auth, no CORS
(single origin: everything reaches the SPA through the hub).

## 5. Verified pi SDK facts (checked against installed 1.5.0; pwb runs 0.87 with identical call shapes)

Everything below exists in this repo's installed
`@earendil-works/pi-coding-agent@1.5.0` (`/usr/local/lib/node_modules/…`).
Pin `^1.5.0` in `sidecar/package.json`.

- **No permission gating exists**: pi "does not ask for approval before every
  tool call" (docs/security.md). A headless session with default tools writes
  `plot.py` unimpeded — this matches design §10 (agent = same privileges as a
  terminal). Nothing to configure.
- `createAgentSession(options?)` accepts exactly: `cwd`, `agentDir`,
  `modelRuntime`, `model`, `thinkingLevel`, `scopedModels`, `noTools`, `tools`,
  `excludeTools`, `customTools`, `resourceLoader`, `sessionManager`,
  `settingsManager`, `sessionStartEvent`. Result:
  `{session, extensionsResult, modelFallbackMessage?}`.
- `DefaultResourceLoader({ cwd, agentDir, noExtensions?, noPromptTemplates?,
  noThemes?, noContextFiles?, … })` then `await loader.reload()`. Omit
  `noContextFiles` — context files are the skill-file channel. No
  `systemPromptOverride`: pi's default system prompt + workspace `AGENTS.md`
  is the whole prompt story (design §8).
- `SessionManager.create(cwd, sessionDir?)` — fresh session file (New chat);
  `SessionManager.continueRecent(cwd, sessionDir?)` — resume latest or create
  (boot). **A session file is only created once a user/assistant message
  exists** (session-manager.d.ts) — an idle fresh boot leaves `.chat/` empty;
  don't assert a file appears at boot.
- `AgentSession`: `subscribe(listener) -> unsubscribe`, `prompt(text)` (resolves
  when the full run incl. retries finishes), `abort(): Promise<void>`,
  `dispose()`, `messages`, `sessionFile?: string`, `model?: Model`,
  `getLastAssistantText()`.
- Session events (via `subscribe`): `message_update` carrying
  `event.assistantMessageEvent` (filter `type === "text_delta"`); `agent_start`;
  `agent_end` (per low-level run — carries `willRetry`; NOT run completion);
  `tool_execution_start` / `tool_execution_end` (`toolName`, `isError`);
  `agent_settled` (fires when Pi will not continue automatically — informative,
  not needed for the busy flag, which keys off `prompt()` resolution).
- `prompt()` on an already-streaming session **rejects** unless steer/followUp
  is chosen — the busy-409 guard makes this unreachable (trap T1).
- `ModelRuntime.create()` → `runtime.getModel(provider, id)` (`undefined` if
  unknown); auth/models come from `~/.pi/agent` — no keys in better-mad.
- Default model: pass **no** `model` to `createAgentSession` → user's pi
  default, else first available. Report the resolved id from
  `session.model` → `` `${provider}/${id}` `` in `/health`.
- Boot CAN fail: missing `~/.pi/agent`, unknown
  `--model` spec, provider auth errors — the sidecar must exit non-zero with a
  one-line stderr message; the manager surfaces it as the down-reason (§7).

## 6. Sidecar design

### 6.1 `session.ts` — factory (adapted from pwb `sessions.ts`; drop personas/skills/custom tools)

```ts
import { createAgentSession, DefaultResourceLoader, getAgentDir,
         ModelRuntime, SessionManager } from "@earendil-works/pi-coding-agent";

// workspace .chat dir must exist before SessionManager scans it
export async function createChatSession(workspace: string, modelSpec?: string) {
  const loader = new DefaultResourceLoader({
    cwd: workspace,
    agentDir: getAgentDir(),
    noExtensions: true, noPromptTemplates: true, noThemes: true,
    // context files ON — AGENTS.md loads from cwd
  });
  await loader.reload();

  let model;                                   // undefined → pi default
  if (modelSpec) model = await resolveModel(modelSpec);   // throws with a clear message

  const sessionManager = SessionManager.continueRecent(workspace, chatDir(workspace));
  const { session, modelFallbackMessage } = await createAgentSession({
    cwd: workspace, resourceLoader: loader, sessionManager,   // builtin tools: default ON
    ...(model ? { model } : {}),
  });
  return { session, modelId: modelIdOf(session), fallback: modelFallbackMessage };
}
// fresh-session variant for /new: identical but SessionManager.create(...)
```

### 6.2 `manager.ts` — ChatManager (lift pwb `PersonaManager`, single agent, no daily rotation, no context composition)

- State: `{ runtime, listeners: Set, inFlight?: Promise, stopRequested, resetting }`.
- `send(text)`: **synchronous** busy check → throw BusyError (route → 409);
  then async run wrapped in `runTurnWithWatchdog` (lift verbatim — it is
  framework-free; default 5 min): `prompt: () => session.prompt(text)`,
  `abort: () => session.abort()`, on-timeout broadcasts a calm `error` +
  releases busy. The returned promise **never rejects** — failures broadcast
  `error`. `stopRequested` → broadcast `stopped` (not `error`) after an abort.
- `stop()`: `false` when idle (route → 409); else set `stopRequested`,
  `void session.abort().catch(() => {})`.
- `new()`: BusyError if in flight/resetting; create fresh session via the
  factory variant, **re-wire events on the new session**, `dispose()` the old,
  broadcast `reset`. Old `.chat/*.jsonl` stays on disk (UX §8).
- `history()`: pwb's `historyForClient` projection verbatim (role + text join,
  skip empty).
- `wireEvents`: `message_update`→text_delta passthrough, `agent_start/end`,
  `tool_execution_start/end` → `ClientEvent` broadcast to all listeners, each
  listener call try/caught.
- `dispose()`: abort in-flight (await it), dispose session, clear listeners.

### 6.3 `server.ts` — node:http (port pwb `chat.ts` semantics)

- Exact-match routing for the 6 paths (§4); anything else 404.
- JSON bodies: collect chunks with a 1 MB cap (413), `JSON.parse` in try (400).
- SSE writer = pwb `openEventStream` ported to `res`: immediate
  `writeHead(200, {content-type: text/event-stream, cache-control: no-cache,
  connection: keep-alive, x-accel-buffering: no})`, `retry: 2000\n\n`,
  heartbeat interval 25 s (`: ping\n\n`), unsubscribe + `clearInterval` on
  `res.on("close")`, guard `writableEnded` before writes.
- One `ChatManager` instance; `manager.subscribe(send)` per SSE connection.

### 6.4 `main.ts` — entry, handshake, shutdown

- argv: `--workspace <path>` (required), `--port <n>` (0 = ephemeral; report
  the bound port), `--model <provider/model>` (optional).
- Boot order: `mkdir -p workspace/.chat` → create session → listen → print ONE
  line to stdout: `SIDECAR_READY {"port": N, "model": "provider/id",
  "session_file": "..."}` (mirrors the worker's `WORKER_READY` handshake the
  Python side already knows how to read). `modelFallbackMessage` → stderr warn.
  Boot failure → single stderr line + `process.exit(1)` (§5 last bullet).
- `SIGTERM`/`SIGINT` → `manager.dispose()` (abort wedged turns) →
  `server.close()` → exit 0. The hub's `stop()` sends terminate-then-kill.

## 7. `hub/sidecar.py` — SidecarManager (mirror `worker/manager.py`)

```python
class SidecarManager:
    def __init__(self, workspace: Path, *, command: list[str] | None = None,
                 model: str | None = None, health_interval_s: float = 2.0): ...
    async def start(self) -> None      # node gate → spawn → ready handshake → health loop
    async def stop(self) -> None       # terminate, wait 5 s, kill
    def chat_info(self) -> dict        # {"state", "port", "model", "restarts", "reason"?}
    def base_url(self) -> str | None   # http://127.0.0.1:<port> when ready
    def subscribe(self, cb) -> Callable[[], None]   # chat state events (worker pattern)
```

1. **Command**: default `["node", "<sidecar_dir>/src/main.ts", "--workspace",
   str(ws.path), "--port", "0"]` (+ `["--model", spec]` when given). Locate
   `<sidecar_dir>` like `_default_static_dir()` probes (cwd, then repo root
   relative to `__file__`); overridable via the `command` kwarg (tests inject a
   stub; devs may point `BETTER_MAD_SIDECAR` at an alternate entry).
2. **Node gate (R1)**: `shutil.which("node")` missing → state `down`, reason
   `"node not found — chat disabled (install Node ≥22.18)"`. Then
   `node --version` < 22.18 → `down`, reason mentions the version floor
   (native `.ts` execution). Never spawn on a failed gate.
3. **Spawn + handshake**: Popen(stdout=PIPE, stderr=PIPE-with-file…) reading
   stdout lines until `SIDECAR_READY {json}` (timeout 60 s — pi SDK import is
   heavy, pwb boots in seconds). stderr → `<workspace>/.data/sidecar.log`
   (append; mirror worker.log). Handshake failure → capture last stderr line
   as `reason`, one restart attempt, else `down`.
4. **Health loop**: GET `/health` every `health_interval_s` (2 s timeout);
   process death needs no HTTP (`poll()`). A hung turn is NOT unhealthiness —
   `/health` stays responsive while `busy: true`.
5. **Restart-once-then-down** (design §9.1 — stricter than the worker's 3):
   crash-loop guard with `restart_limit=1` per 60 s window. Second death in
   the window → state `down` + event + `reason`; hub and worker keep running;
   `/api/chat/*` → 503.
6. **States/events**: `starting | ready | restarting | down` (+ `reason`,
   `port`, `model`, `restarts`). Emit on every transition, worker-manager
   style. No epoch — the SPA doesn't re-embed on chat restart, it just
   reconnects SSE.

## 8. Hub app + CLI edits

- **Proxy routes** (`hub/app.py`): for each of
  `events|send|stop|new|history` — same path under `/api/chat/`, forwarded to
  the sidecar's identical path (§4). Non-SSE: `await` one shared
  `httpx.AsyncClient`, map status + body through. Sidecar down/starting →
  `503 {"error": "chat sidecar unavailable", "reason": …}`. **SSE passthrough**:
  `httpx` `client.stream(...)` with read-timeout disabled, re-yield raw bytes
  in a `StreamingResponse` (`media_type="text/event-stream"`, W8 headers);
  `finally:` closes the upstream stream on client disconnect — never
  interpret, buffer, or rewrite payloads (the hub parses nothing beyond
  transport, design §4.2).
- **Status**: `/api/health` and `/api/status` gain `"chat":
   sidecar.chat_info()`; `/api/events` gains `{"type": "chat", "state": …,
   "port", "model", "restarts", "reason"?}` on transitions (emitted through
   `HubState` exactly like `_on_manager_event`). Extend the reserved-path
   comment.
- **Lifespan**: `HubState.start/stop` also start/stop the `SidecarManager`
  (keep both idempotent — tests call them directly, W6 pattern).
- **CLI**: add `--model provider/model` (format-validated only — real
  resolution is Node-side); `main()` constructs `SidecarManager(ws.path,
   model=args.model)`, passes it to `create_app`. Chat boot failure never
   blocks the hub (the manager's `down` state handles it silently).

## 9. npm/TS setup (native type stripping)

`sidecar/package.json`:

```json
{ "name": "better-mad-sidecar", "private": true, "type": "module",
  "engines": { "node": ">=22.18" },
  "scripts": { "typecheck": "tsc --noEmit", "test": "vitest --run" },
  "dependencies": { "@earendil-works/pi-coding-agent": "^1.5.0" },
  "devDependencies": { "typescript": "^5.6", "vitest": "^4.0", "@types/node": "*" } }
```

`tsconfig.json`: `strict`, `module: "nodenext"`, `moduleResolution: "nodenext"`,
`target: "es2022"`, `noEmit: true`, `allowImportingTsExtensions: true`,
`verbatimModuleSyntax: true`, `types: ["node"]`. **Verified 2026-11-10**:
this exact combo (with the `"type": "module"` package.json above) typechecks
clean and runs under `node` natively; the `.ts`-extension imports resolve at
runtime. Node fact (release notes, verified): **22.18.0 = "Type stripping is
enabled by default"**; 24.x current here runs `.ts` unflagged.

**Erasable-only rules (trap T6)**: imports use explicit `.ts` extensions
(`./manager.ts`); no enums/namespaces/parameter properties (use string unions
and explicit fields — pwb's `ClientEvent` already does); every type-only
import is `import type`. `npm install` runs inside `sidecar/` (no root
workspace file yet — that lands with `web/` in M3.4).

## 10. Traps

| # | Trap | Rule |
|---|------|------|
| T1 | `session.prompt()` while streaming rejects (steer/followUp ambiguity) | The busy-409 guard is the only prompt path — never call `prompt()` re-entrantly |
| T2 | `agent_end` fires per low-level run (`willRetry` may follow) | Busy-release keys off `prompt()` resolution; `agent_end` is display telemetry only |
| T3 | `message_update` carries other `assistantMessageEvent` subtypes | Filter `type === "text_delta"` before broadcasting |
| T4 | Subscriptions bind to the `AgentSession` instance | `/new` must re-wire events on the NEW session before disposing the old (pwb `swapSession` order) |
| T5 | `dispose()` aborts in-flight work; `abort()` is async | SIGTERM handler: `await manager.dispose()`; fire-and-forget aborts get `.catch(() => {})` |
| T6 | Native TS = erasable syntax only; type-only named import kept verbatim → runtime `SyntaxError: does not provide an export named 'X'` | `import type` everywhere a binding is only a type; `verbatimModuleSyntax` makes tsc enforce it |
| T7 | Node <22.18 parses `.ts` as JS → confusing SyntaxErrors | The manager's version gate (§7.2) converts this to a clean `down` + reason |
| T8 | node:http SSE leaks | `res.on("close")` → unsubscribe + clear heartbeat; guard `writableEnded`; writeHead immediately |
| T9 | httpx buffers/proxies SSE badly | Read-timeout off on the stream client; yield raw chunks; `finally` closes upstream on disconnect; test against a real socket (M3.2 deviation-1 pattern) |
| T10 | Missing `~/.pi/agent` / bad `--model` / provider auth errors | Sidecar exits 1 with one clear stderr line; manager captures it as `reason`, restarts once, then `down` — never a tight crash loop |
| T11 | Session files appear "late" | A file is written only after the first message — don't wait for it at boot or in tests |
| T12 | `npm install` not run in `sidecar/` | Spawn fails (module not found) → same restart-once→down path; mention `npm install` in the reason string if `node_modules` is absent |
| T13 | `nodenext` infers module kind from the nearest `package.json` | Missing `"type": "module"` → every file parsed as CJS → `TS1295` under `verbatimModuleSyntax` on the first import; the sidecar `package.json` carries it — don't drop it |

## 11. Tests

**Sidecar (vitest, session-stubbed — inject a fake `AgentSession` via a
`forTest` seam exactly like pwb's `manager.test.ts`/`manager.stop.test.ts`;
no network, no model):** send happy path broadcasts text_delta…agent_end;
busy second send → BusyError; stop while idle → false; stop mid-turn →
`stopped` (not `error`); watchdog timeout → abort + error broadcast + busy
released; `new` swaps session, re-wires events, broadcasts `reset`, refuses
while busy; history projection skips empties; server: empty text → 400,
unknown path → 404, two SSE listeners both receive broadcasts, heartbeat
interval cleared on close (fake timers).

**Hub (pytest, against a stub sidecar — a tiny `http.server` script under
`tests/fixtures/` implementing `/health` + the contract, injected via
`command=`; no Node, no ~/.pi in CI):** manager spawn → `SIDECAR_READY` →
ready; kill -9 → restarting → ready (restarts=1); second kill in window →
down + reason + hub `/api/health` still `ok:true`; `command=["nonexistent"]`
→ down with node-not-found-style reason; proxy: `/api/chat/send` 202/409
mapped, sidecar down → 503; SSE passthrough smoke via real uvicorn on an
ephemeral port (reuse the M3.2 SSE-test fixture shape) — stub emits one
`text_delta`, client reads it through the hub, disconnect closes upstream.

**Manual smoke (exit criteria — document in the commit message, live model):**
fixture workspace (`tests/fixtures/sample_ws.txt` via the hub), real sidecar,
real model from your pi config; curl `/health` → `send` "write a hello
plot.py" → SSE shows text_delta + tool_execution_start/end (write tool) →
`plot.py` content changes on disk → `history` replays the turn → `new` →
`reset` + fresh `.chat/` file → hub end-to-end through `/api/chat/*`.

## 12. Do / don't

DO: lift pwb's `runTurnWithWatchdog`, busy/stop/reset state machine, event
wiring, `resolveModel`, and `historyForClient` (cite the source files in
comments); keep the sidecar a dumb pi-session host (design §9.1 — no
better-mad imports, no custom tools, no dataset knowledge); make every hub
endpoint total (503 beats exception); update docs in the same commits
(§13).

DON'T: build any chat UI (M3.4); add fastify/tsx/esbuild or any sidecar dep
beyond the pi SDK; modify `core/`, `sdk/`, `worker/`; let the hub parse or
rewrite SSE payloads (bytes in, bytes out); add auth/CORS; touch `~/pwb`.

## 13. Documentation updates (same commits, per convention)

- `design.md` §9 core bullet: remove "sidecar lifecycle" from the core list
  (it lives in the hub, mirroring `worker/manager.py`); note Node ≥22.18 in
  §9's stack line.
- `AGENTS.md`: commands section — `npm install` (in `sidecar/`), Node floor
  ≥22.18; "Current status" → M3.3.
- `PLAN.md`: tick the M3.3 exit line ("✔ done" + one-line summary).

## 14. Definition of done

- [ ] `sidecar/` per §6 (manager/session/server/main) + package/tsconfig per §9
- [ ] Contract §4 exact (paths, codes, payloads, heartbeat, retry line)
- [ ] `hub/sidecar.py` + proxy routes + chat status/events + CLI `--model`
- [ ] vitest suite (§11) + pytest suite (§11) green; whole `uv run pytest` green
- [ ] `uv run ruff check .` + `uv run ty check` + `npm run typecheck` clean
- [ ] Manual smoke §11 (live model, hello-plot.py file produced, SSE text +
      tool events, hub-proxy end-to-end) documented in the commit message
- [ ] Doc updates §13; PLAN.md M3.3 ticked

Report at the end: files changed, test counts, deviations from this document
(with justification), surprises.
