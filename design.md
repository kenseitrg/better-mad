# better-mad — Design (v2: agent-driven plotting)

An LLM-agent-driven instrument for visualizing seismic attributes: load large tabular
text files, tell an agent what you want to see, get back a plotting **script** plus an
**interactive preview** — then refine either by hand or through the agent.

The v1 design (declarative plotting UI, milestones M0–M4 implemented) is preserved on
the `archive/codebase` branch.

---

## 1. Decisions (resolved)

| # | Topic | Decision |
|---|-------|----------|
| 1 | Product direction | **Instrument, not omnibus**: no attempt to menu-ify every plot type. Users get a programmable surface (agent + editable code) they can tailor |
| 2 | Agent integration | **Embedded terminal** (pty) in the left panel. Harness-agnostic: the user runs their preferred agent CLI (pi, claude-code, codex, aider, local-model CLI…). No SDK/chat embedding in v2 |
| 3 | Agent ↔ app protocol | **Filesystem**. The agent edits the plot script; the app watches the file and re-runs it. No sockets, no proprietary protocol |
| 4 | Plotting stack | **HoloViews + Panel + Datashader** (carried over from v1; handles 1–2M-row files natively) |
| 5 | Code execution | **Subprocess runner** with a small SDK (`show()` transport). Generated code never executes inside the app/server process |
| 6 | Script model | One active script (`plot.py`) per workspace in v2. Multiple scripts/tabs: future work |
| 7 | Code editing | Center pane toggles **Preview ⟷ Code**; the editor edits the same `plot.py` the agent writes. External changes reload the editor |
| 8 | Data loading | Reuse the v1 core loader from `archive/codebase`: delimiter detect, hostile-header sanitization, configurable null sentinels, float32, parquet cache |
| 9 | Agent knowledge | Workspace ships an **`AGENTS.md` skill file + auto-generated dataset manifest** so even small local models can produce correct plots by adapting recipes |
| 10 | Sandbox | **None.** Scripts run with the user's own privileges — same trust model as running the agent in a normal terminal. Documented, not hidden |
| 11 | Distribution | Unchanged: CLI starts a Panel server on localhost, opens the system browser |

---

## 2. Concept

Instead of building the perfect plotting UI for every case, better-mad provides an
instrument the user tailors:

```
import files → describe intent to the agent → agent writes plot.py
            → app runs it → interactive preview
            → adjust: edit the code by hand, or talk to the agent
```

- The **agent does the composition**: picking columns, choosing plot types, styling,
  datashader usage — all the things v1 expressed as menus.
- The **code is the configuration**: fully visible, fully editable, version-controllable.
- The **app provides the substrate**: robust loading of hostile 1–2M-row files, a safe
  runner, a live interactive preview, and skill material that makes small models capable.

## 3. Layout

```
┌──────────────────────────────────────────────────────────────────┐
│ Header: workspace · [Run] [Auto-run ✓] · run status              │
├────────────────┬─────────────────────────────┬───────────────────┤
│ Agent terminal │  Center                     │ Files             │
│ (any CLI       │  [Preview | Code] toggle    │ ▸ file_a 708k rows│
│  harness,      │  • live interactive plot    │   columns + stats │
│  user's choice)│  • or editable plot.py      │ ▸ file_b …        │
│                │                             │ [Add files…]      │
└────────────────┴─────────────────────────────┴───────────────────┘
```

- **Left — terminal**: pty-backed terminal widget; the app spawns a shell there, the
  user launches whatever agent harness they like. cwd is the workspace directory.
- **Center — preview/code**: the rendered figure (HoloViews pane) or a code editor bound
  to `plot.py`. Toggle is instant; both stay alive.
- **Right — files**: open datasets with columns (original display names + sanitized
  names), row counts, quick stats; import entry point.

## 4. Workspace & agent protocol

The app owns a **workspace directory** (created per session; path shown in header):

```
workspace/
├── AGENTS.md        # skill file: SDK reference, conventions, recipes, pitfalls
├── datasets.md      # auto-generated manifest of loaded data (names, columns, stats)
└── plot.py          # the one active plotting script (agent- and user-edited)
```

- The terminal starts with cwd = workspace, so any harness picks up `AGENTS.md`
  according to its own conventions — no per-harness integration needed.
- `datasets.md` is regenerated on every import/re-parse: file names, row counts,
  original + sanitized column names, dtype, min/max/NaN%. This is what lets a small
  model know the data without reading 121 MB files.
- A **file watcher** (debounced ~0.5 s) detects changes to `plot.py` from any writer
  (agent, user's editor, external editor) and triggers a run when auto-run is on.
- Manual **Run** button always available; auto-run is a toggle.

## 5. SDK & runner

Scripts run in a **subprocess** and talk to the app through a tiny SDK:

```python
import better_mad.sdk as bm

df = bm.data("file_a")          # DataFrame from the app's loaded datasets
print(bm.list_data())           # available dataset names + shapes

pts = hv.Points(df, ["XCORD_MIDPT", "YCORD_MIDPT"], vdims=["TR_DOMFREQ"])
bm.show(hv.render(pts, ...))    # or just: bm.show(pts)
```

- `bm.data(name)` returns the parsed DataFrame (from the parquet cache — instant).
- `bm.show(obj)` serializes the HoloViews object to an app-provided output path
  (pickle, local-only transport). Accepted: `hv.Element`, `Overlay`, `Layout`,
  `NdLayout`. Last `show()` wins.
- The runner captures **stdout/stderr**, enforces a **timeout** (default 60 s,
  configurable), and returns: figure, run time, stderr tail.
- On failure: **the last good plot stays up** with a staleness badge; the error tail
  renders as a banner in the center pane. Never a crash, never a modal.
- The runner is core (headless, pytest-covered); the preview pane is a thin consumer.

## 6. Input data (carried over from v1)

All v1 loader requirements stay — they are exactly why this tool exists:

- Whitespace/comma/tab-delimited text, first row header, no extensions, hostile names
  (`TR.DOMFREQ`, `3DT_SEC_ORD_CELCTR`) → sanitized internal names, originals kept for
  display. Ragged/comment rows tolerated. Never crash the load.
- Configurable null sentinels per file (numeric or string tokens); **0 is always valid**.
- float32 attributes / float64 coordinates; parquet cache in `~/.cache/better-mad/`
  keyed by path+mtime+size+settings.
- Target: 1–2M rows × several files open. (Measured in v1: 708k first load ≈11 s,
  cache reload ≈0.05 s, 31 MB RAM/file.)

The loader, column registry, and cache are restored from `archive/codebase` (they are
UI-free and fully tested).

## 7. Preview interactivity

The preview must keep the core exploratory interactions:

- **Pan / wheel-zoom / box-zoom / reset** via the standard Bokeh toolbar.
- **Box & lasso selection** enabled on the toolbar.
- Vector hover and linked selection beyond the toolbar are **script-side**: the agent or
  user adds `hv.streams`/`HoverTool` in `plot.py`; AGENTS.md ships recipes.
- **Datashader is script-side too** (recipes: `rasterize(...)` for >~100k points,
  mean-of-z aggregation, `cnorm`/clim handling). The skill file encodes the v1 lessons
  (rasterize aggregates the first vdim, etc.).

## 8. Agent skills & small-model support

A core bet of v2: with good skills, even a small local model produces good plots.

- `AGENTS.md` in the workspace: SDK reference (copy-pasteable), hard rules (always read
  `datasets.md`, always end with `bm.show()`), and **recipes** for the seismic staples:
  colored scatter map (UTM, equal aspect), crossplot, histogram, line/CMP gather,
  datashader variants, percentile-clipped color scales.
- Every recipe is a complete runnable script, not pseudocode.
- Known-gotchas section carries the v1 post-mortems (pandas 3.x parsing, rasterize
  vdim trap, sizing policies) so models don't rediscover them.
- `datasets.md` keeps per-column stats so the model can pick sane clims/bins without
  loading data.

## 9. Tech stack & architecture

- Python ≥ 3.11, **uv**, **ruff** + **ty**.
- Deps: pandas, pyarrow, holoviews, panel, datashader. (watchdog optional — polling
  fallback is fine.)
- **UI-free core** (rule carried over): `better_mad/core` = loading, workspace model,
  runner, SDK, manifest generation. All headless, all pytest-covered.
  `better_mad/app` = Panel shell: terminal pane, preview/code center, files panel.
- CLI: `better-mad [files...]` → workspace + server + browser, as in v1.

## 10. Security model

- Generated scripts run **unsandboxed**, with the user's privileges, on the user's
  machine — identical to running the agent in a plain terminal. The app does not fetch,
  exec, or auto-run anything from the network; execution only follows edits to `plot.py`.
- The trust boundary is the agent harness itself; better-mad stays out of that decision.
- Documented plainly in README and first-run hint.

## 11. Future work (explicitly not v2, ordered)

1. Multiple scripts / script tabs, script history & diff
2. Embedded agent SDK chat panel as an alternative to the terminal (same workspace
   protocol — the design intentionally doesn't block this)
3. Selection → data workflows: export selected points to CSV, feed selection back to
   the agent ("plot only these")
4. Session persistence: files + parser settings + `plot.py` + workspace path
5. PNG export button (HoloViews matplotlib backend, as planned in v1)
6. Hover/tap inspection helpers as SDK/recipe extensions
7. Cross-file joins/expressions (if still needed once the agent can do them in code)
8. AppImage packaging
9. Gridding/interpolation for map views (still explicitly out by default)
