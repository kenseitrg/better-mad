# better-mad — UX Specification (v2)

How the user interacts with the agent-driven instrument. Complements `design.md`
(what) with the interaction model (how).

## Decisions (resolved)

| # | Topic | Decision |
|---|-------|-----------|
| 1 | Left panel | **Embedded chat panel** with the pi agent (Node sidecar, design §9.1) — replaces the originally-planned pty terminal (2026-11-02) |
| 2 | Center panel | Single pane toggling **Preview ⟷ Code**; both views stay alive so switching is instant |
| 3 | File I/O | Data import via right-panel picker; everything else stays on disk in the workspace |
| 4 | Re-run trigger | **Auto-run on file change** (debounced, toggleable) + manual Run button |
| 5 | Failure handling | Last good plot stays up with a staleness badge; error tail as a banner. No modals |
| 6 | Chat turns | One in-flight turn; **Stop** aborts; **New chat** rotates the session (old history stays on disk). Agent actions surface as compact tool notes, never raw protocol noise |
| 7 | Frontend | **Web-native React/TS SPA** + FastAPI hub; Panel dropped 2026-11-02 (look & feel, framework fights). Plot pane = **live bokeh document** (re-aggregates on zoom) |

---

## 1. App shell

```
┌──────────────────────────────────────────────────────────────────┐
│ Header: workspace path · [▶ Run] [Auto-run ✓] · ✓ ran 0.8 s ago  │
├────────────────┬─────────────────────────────┬───────────────────┤
│                │  [● Preview] [ Code ]       │ Files             │
│ Agent chat     │  ┌────────────────────────┐ │ ▾ file_a (708k×9) │
│                │  │                        │ │   XCORD_MIDPT …   │
│ ▸ user: color  │  │    interactive plot    │ │   TR.DOMFREQ …    │
│ ◂ agent: done, │  │                        │ │ ▸ file_b …        │
│   wrote plot.py│  └────────────────────────┘ │                   │
│ ⚙ edit plot.py │  (or: editor for plot.py)   │ [＋ Add files]    │
│ [input…][Send] │       [■ Stop] [New chat]   │                   │
└────────────────┴─────────────────────────────┴───────────────────┘
```

- All three columns are drag-resizable; widths persist per browser (localStorage),
  same mechanism as v1 (CSS variables, never inline widths).
- The shell is a web-native SPA — full CSS control; the plot pane embeds the render
  worker's live bokeh document, so plot interactions are real server-side updates,
  not baked images.
- The header status line always answers: *did my last change make it to the plot?*
  (running…, ✓ time+rows, ✗ error with one-line reason).

## 2. First launch / empty workspace

1. App opens with an empty workspace directory created and the chat panel ready
   (input enabled once the sidecar reports healthy; otherwise a reconnect banner).
2. Center shows the empty state: "Add files (right), then tell the agent what to plot."
3. Right panel offers **＋ Add files**.
4. The chat panel greets with a one-time hint: *"AGENTS.md and datasets.md in this
   directory teach me the SDK and your data."*

## 3. Importing files

- **＋ Add files** → browser file picker → files parse immediately with best-guess
  defaults (v1 behavior): delimiter auto-detect, default sentinels, decimal `.`.
- Each file entry expands to: row/column counts, parse status, and a collapsible
  **Import settings** block (delimiter, decimal separator, sentinel list, first-20-rows
  preview, per-column parse status). Editing exposes **Re-parse** — nothing re-loads
  silently.
- On success: file appears in the right panel with its columns (display names, min/max,
  NaN%); `datasets.md` in the workspace is regenerated immediately.
- Wrong parses must be *visible* (status + counts), never silent.

## 4. The agent loop (core flow)

1. User types intent in the chat panel: *"color-scatter map of TR.DOMFREQ on the
   midpoints, datashaded, percentile-clipped colors"*.
2. The agent session (sidecar) already has AGENTS.md as context; it reads
   `datasets.md`, writes `plot.py`. Tool calls surface as compact notes
   ("⚙ edit plot.py…") while it works.
3. The watcher fires (debounced ~0.5 s) → runner executes → **preview updates**.
4. Header shows ✓ run time + rendered points. User inspects with pan/zoom/select.
5. Refinement goes either way:
   - *"make the colormap turbo and add a colorbar"* → back to step 2.
   - Toggle **Code**, tweak a line, save → auto-run → back to **Preview**.

## 5. Code view

- Center toggle: **Preview | Code**. The Code tab is a full editor
  (syntax highlighting, line numbers) bound to `plot.py`.
- Save: explicit **Ctrl+S** and debounced auto-save on focus loss. Saving triggers a
  run exactly like an agent edit.
- **External change policy**: if `plot.py` changes on disk while the editor buffer is
  clean → reload silently. If the buffer is dirty → banner: *"plot.py changed on disk
  (agent?). [Reload] [Overwrite]"* — agent output is never silently clobbered, and
  neither is the user's edit.
- Run errors are also visible inline: a collapsed **Output** drawer under the editor
  with the last run's stdout/stderr.

## 6. Preview behavior

- **Live document**: zoom/pan/hover/selection are server-real. On datashaded plots
  every range change re-rasterizes on the worker (~0.1–0.3 s) and streams back —
  no re-run, no static-image feel.
- Bokeh toolbar on every plot: pan, wheel zoom, box zoom, box select, lasso select,
  reset, save (Bokeh PNG).
- The preview pane holds the **last good figure**:
  - Script fails → figure gains a staleness badge ("⚠ plot.py failed 12:03 — showing
    last good result"), error tail as a banner with [Open output].
  - Script runs but calls no `show()` → placeholder "Script ran but produced no figure."
  - Script times out → kill + "Run timed out after 60 s" banner.
- Interactions beyond the toolbar (hover, linked selection) are the script's job;
  the app never strips or overrides tools the script configured.

## 7. Right panel — files & columns

- One card per loaded file: name, rows×cols, parse status; expand → columns with
  **original display names** and quick stats (min/max/NaN%).
- Column names are **click-to-copy** (copies the sanitized name scripts must use, with
  a tooltip showing the mapping). Rationale: the consumer of column names in v2 is a
  text script, so copy beats a picker.
- File actions: re-parse settings, reload from disk, close (with confirm if the script
  references it).
- Closing a file referenced by `plot.py` doesn't kill anything — the next run fails
  with a clear `bm.data()` KeyError that the banner surfaces.

## 8. Chat behavior

- The chat pane streams assistant replies token-by-token; agent tool calls render as
  one-line notes ("⚙ editing plot.py…") so the user sees progress without protocol noise.
- **One in-flight turn**: the input is disabled while the agent works; **Stop** aborts
  the turn and leaves a calm "Stopped." marker; the partial reply stays visible.
- **New chat** rotates to a fresh session file (old transcript stays on disk);
  refused while a turn is in flight.
- Chat history persists per workspace and replays after app restart / tab reload.
- The app never intercepts or rewrites agent actions — the only coupling to the agent
  is the filesystem (design §4). The transport carries text and telemetry for display.
- If the sidecar is down: chat pane shows a reconnect banner with a retry; preview,
  code editor, and files panel keep working (they never depend on the agent).
- The resolved model id is shown in the chat header; changing models is a CLI flag
  (`--model provider/model`) in v2 — no in-app model picker.

## 9. Errors & empty states

Everything degrades to banners/badges/placeholders — never crashes, never modals
except destructive confirms:

| Situation | Handling |
|-----------|----------|
| Unparsable file | Error in its import block; file kept for settings tweaks |
| Script error / timeout | Last good plot + staleness badge + error banner |
| No `show()` call | Center placeholder with hint |
| Dataset missing at run time | Banner quoting the `bm.data()` KeyError |
| Agent overwrote dirty editor buffer | Reload/Overwrite banner (§5) |
| Chat sidecar down / crashed | Banner in chat pane with retry; preview/editor/files unaffected |
| Agent turn hung | Watchdog aborts after 5 min; "Stopped." marker; input re-enabled |
| Worker hung / crashed | Hub kills + restarts it, restores last good plot from snapshot; banner; no auto re-run (no crash loops) |
| First launch | Quick-start card (§2) |

Modals only for: closing a referenced file, overwriting a dirty `plot.py`,
deleting the workspace.
