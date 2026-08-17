# better-mad — UX Specification (v2)

How the user interacts with the agent-driven instrument. Complements `design.md`
(what) with the interaction model (how).

## Decisions (resolved)

| # | Topic | Decision |
|---|-------|-----------|
| 1 | Left panel | **Embedded terminal** running the user's own agent harness — not a bespoke chat UI |
| 2 | Center panel | Single pane toggling **Preview ⟷ Code**; both views stay alive so switching is instant |
| 3 | File I/O | Data import via right-panel picker; everything else stays on disk in the workspace |
| 4 | Re-run trigger | **Auto-run on file change** (debounced, toggleable) + manual Run button |
| 5 | Failure handling | Last good plot stays up with a staleness badge; error tail as a banner. No modals |

---

## 1. App shell

```
┌──────────────────────────────────────────────────────────────────┐
│ Header: workspace path · [▶ Run] [Auto-run ✓] · ✓ ran 0.8 s ago  │
├────────────────┬─────────────────────────────┬───────────────────┤
│                │  [● Preview] [ Code ]       │ Files             │
│ Agent terminal │  ┌────────────────────────┐ │ ▾ file_a (708k×9) │
│                │  │                        │ │   XCORD_MIDPT …   │
│ $ pi           │  │    interactive plot    │ │   TR.DOMFREQ …    │
│                │  │                        │ │ ▸ file_b …        │
│                │  └────────────────────────┘ │                   │
│                │  (or: editor for plot.py)   │ [＋ Add files]    │
└────────────────┴─────────────────────────────┴───────────────────┘
```

- All three columns are drag-resizable; widths persist per browser (localStorage),
  same mechanism as v1 (CSS variables, never inline widths).
- The header status line always answers: *did my last change make it to the plot?*
  (running…, ✓ time+rows, ✗ error with one-line reason).

## 2. First launch / empty workspace

1. App opens with an empty workspace directory created and the terminal ready.
2. Center shows the empty state: "Add files (right), then tell your agent what to plot."
3. Right panel offers **＋ Add files**.
4. The terminal prints a one-time hint: *"Run your agent here — AGENTS.md and
   datasets.md in this directory teach it the SDK and your data."*

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

1. User starts their harness in the terminal (`pi`, `claude`, `codex`, local-model CLI…).
2. User types intent in natural language: *"color-scatter map of TR.DOMFREQ on the
   midpoints, datashaded, percentile-clipped colors"*.
3. The harness reads `AGENTS.md`/`datasets.md`, writes `plot.py`.
4. The watcher fires (debounced ~0.5 s) → runner executes → **preview updates**.
5. Header shows ✓ run time + rendered points. User inspects with pan/zoom/select.
6. Refinement goes either way:
   - *"make the colormap turbo and add a colorbar"* → back to step 3.
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

## 8. Terminal behavior

- The terminal is a real pty + shell; the app spawns no agent itself and has no
  opinions about the harness. Scrollback, copy/paste, and resize work as in any
  terminal widget.
- The app never parses, intercepts, or injects terminal traffic — the only coupling
  to the agent is the filesystem (§4 of design.md).
- **Restart shell** action in the panel header for wedged sessions.

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
| First launch | Quick-start card (§2) |

Modals only for: closing a referenced file, overwriting a dirty `plot.py`,
deleting the workspace.
