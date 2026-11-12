# M3.4 manual demo checklist

The full stack is **already running** (verified 2026-11-11 by `scripts`-style QA pass,
20/21 automated checks green — the one miss was a benign reconnect race, see Notes):

- hub + SPA (prod build): **http://127.0.0.1:5006/** — workspace `/tmp/bm34/ws`
- data: `data/14_01_post_stack_attr_after_scac_all` (708,650 rows × 9 cols)
- worker: ready, epoch 2 · sidecar: ready, model `zai/glm-5.3`
- chat history: fresh (empty) · last-good plot (agent-written scatter) is up
- screenshots of the automated pass: `/tmp/bm34/shots/` · QA script: `/tmp/bm34/m34_qa.py`
- hub log: `/tmp/bm34/hub.log`; stop everything with
  `pkill -f "bin/better-mad" ; pkill -f "better_mad.worker" ; pkill -f "sidecar/src/main.ts"`

## 1. The exit-criteria loop (the headline demo)

1. Open **http://127.0.0.1:5006/** in a normal browser window, ideally maximized
   (exit criteria were verified at 1920×1080).
2. Chat pane shows the first-run hint; the plot pane shows the last-good scatter.
3. Type into the composer:
   `scatter XCORD_MIDPT vs YCORD_MIDPT colored by TR.DOMFREQ`
   (or a variation — histogram of a column, crossplot, …). Enter sends.
4. Watch: ⚙ tool notes while the agent writes `plot.py`, then the watcher fires,
   status line goes running → ✓, and the live plot appears (~15–25 s total).
5. **Zoom re-rasterization**: mouse-wheel over the plot; pan with drag. The raster
   re-aggregates live (this is the M2-limitation-gone moment).

## 2. Code pane / editor

- Toggle **Code** in the center pane head: CodeMirror shows the agent's `plot.py`,
  Output drawer at the bottom reports the last run (`ok (Xs)`).
- Edit something visible (e.g. change the cmap to `"fire"`), wait ~1 s (auto-save) or
  Ctrl+S → watcher re-runs → back on Preview the plot changed.
- Break it: append `raise RuntimeError('x')` → auto-run → plot pane keeps the last
  good plot + ⚠ banner (`plot.py failed … — showing last good result`) with
  **Open output** (jumps to Code + opens the drawer). Fix it → banner clears.
- External change while dirty: type in the editor (don't save), then let the agent
  overwrite `plot.py` from chat → banner `[Reload] [Overwrite]` (unit-tested logic;
  the racy timing is why it's on the manual list).

## 3. Resilience (the "instrument survives its parts dying" demo)

With the app open, from a terminal:

```bash
kill -9 $(pgrep -f "better_mad.worker --data-dir /tmp/bm34/ws")   # render worker
kill -9 $(pgrep -f "sidecar/src/main.ts --workspace /tmp/bm34/ws") # chat sidecar
```

- **Worker**: banner "render worker restarting…", frozen plot stays; hub restarts it
  (epoch+1), re-runs the last-good script, SPA re-embeds → same plot back (~5–10 s).
- **Sidecar**: chat pane banners "chat unavailable (…) — plot, editor and files keep
  working", composer disabled (~0.3–1.5 s); hub restarts it once and the banner
  clears. Plot zoom/edit/files remain usable the whole time.
- **Reload the page** (F5) at any point: hub `hello` restores everything — plot
  re-embeds without a new run ("✓ last run ok"), chat replays history.

## 4. Smaller things worth clicking

- Auto-run toggle: uncheck → edit `plot.py` (or ask the agent) → nothing runs until
  **▶ Run**. Check it back.
- Chat: **Stop** mid-turn; **New chat** (refused while a turn is in flight).
- Drag the two pane handles; widths persist across reload (localStorage).
- Files pane: expand the dataset card → columns with `original` vs `script` names
  (`TR.DOMFREQ` → `tr_domfreq`, …).
- Model override: restart with `uv run better-mad … --model provider/model`.

## 5. Dev-mode variant (optional)

```bash
uv run better-mad data/14_01_post_stack_attr_after_scac_all --no-browser \
    --workspace /tmp/bm34/ws --dev-origin http://localhost:5173
cd web && npm run dev   # http://localhost:5173
```

(Without `--dev-origin` the live-doc WebSocket 403s and the plot pane stays empty —
check the browser console. In dev always use 5173, never the hub port, so a stale
`web/dist` can't shadow your debugging.)

## Restart from scratch

```bash
pkill -f "bin/better-mad" ; pkill -f "better_mad.worker" ; pkill -f "sidecar/src/main.ts"
cd ~/PROG/better-mad
nohup uv run better-mad data/14_01_post_stack_attr_after_scac_all \
    --no-browser --workspace /tmp/bm34/ws --port 5006 > /tmp/bm34/hub.log 2>&1 &
# fresh workspace instead: --workspace /tmp/bm34/ws2
```

## Notes from the automated pass (things to keep an eye on, not blockers)

- **Chat reset race**: `New chat` immediately after a sidecar restart once showed the
  stale transcript until the next reconnect (SSE resync vs `reset` event — trap T6
  pattern). Repro'd once, not again in isolation (0.06 s). If you hit it: reload or
  click New chat again. Candidate M3.5 polish.
- **Hub log noise**: `kill -9` sidecar logs one `RemoteProtocolError` traceback from
  the chat SSE proxy (peer closed mid-chunk). Expected, recovery verified; the proxy
  could catch it quietly (M3.5 polish).
- Vite build warning "chunk > 500 kB" (CodeMirror) — fine for localhost.
