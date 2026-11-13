# HANDOFF — M3.5: Files panel & polish

You are implementing milestone step **M3.5**, the last step of M3: the files panel
grows up (settings/re-parse, click-to-copy, close-with-confirm), the deferred
no-`show()` placeholder lands, and the shell gets its polish pass (theme, empty
states, two QA leftovers). This file is your single source of instructions. Where
it conflicts with your instincts, this file wins.

M3.1–M3.4 are **done and live-verified** (chat → `plot.py` → live plot → zoom
re-rasterizes at 1920×1080; kill -9 resilience; prod build served by the hub).
Treat them as stable dependencies. Baseline at handover time: **134 pytest,
ruff + ty clean, tsc clean, 52 vitest** (`scripts/check.sh` + web gates).

Decisions already made for you (do not relitigate):

- **Upload persistence (resolves M3.2 trap W9)**: successful browser imports
  persist their **raw file copy** under `workspace/.data/sources/`; the dataset's
  `source` points there, making re-parse and reload-from-disk durable across
  restarts. CLI-loaded files keep their real path (never copied). **Failed imports
  are session-scoped**: the SPA keeps the `File` object in an error card and
  re-uploads with tweaked settings; a page reload drops the card (decided
  2026-11-13).
- **Dead Panel layer is deleted in M3.5** (M3.2 trap W10): `src/better_mad/app/`,
  `tests/test_preview.py`, the `panel>=1.5` dependency (decided 2026-11-13).
- **Close confirm is inline, not a modal** — same banner-style pattern as
  CodePane's Reload/Overwrite (UX §9 permits a modal; we stay consistent with the
  M3.4 precedent and ship no modal system).
- **Re-parse triggers a run when auto-run is on** (visible, user-initiated —
  analogous to a `plot.py` edit; the alternative is a silently stale plot).
  **Close never triggers a run** (UX §7: the next run fails with a surfaced
  `bm.data()` KeyError).
- **Dark theme only** — M3.5 polishes the existing variable-driven dark theme
  (PLAN says "dark theme"; no light mode, no theme switcher).
- Stack rules from M3.4 carry over: no router, no state library, no UI kit, no
  query library; vitest **^3.2** pinned (v4 SIGILLs on this AVX1 CPU).

## 1. Mission

```
FILES PANE (you rebuild FilesPane.tsx)          CENTER (small edits)
┌────────────────────────────────┐              ┌──────────────────────────┐
│ ▾ file_a (708k × 9)  [⚙][⟳][✕] │              │ plot pane:                │
│   columns: click-to-copy        │              │  "Script ran but produced │
│   min/max/NaN%                  │              │   no figure." placeholder │
│   ⚙ Import settings (collap.)   │              │   (ok run + has_figure=0) │
│     delimiter/decimal/sentinels │              │ quick-start card when no  │
│     first-20-rows preview       │              │  datasets & no ok run     │
│     [Re-parse]                  │              └──────────────────────────┘
│ ▸ file_b …                      │              WORKER (one field)
│ ✗ bad.file — parse error        │              RunResult += has_figure
│   [settings… Re-parse] [Dismiss]│              (deferred here from M3.4)
└────────────────────────────────┘
   │ REST /api (hub) — new: reparse · close · head
   ▼
   HUB → CORE: Workspace.add_upload / reparse / remove / dataset_head
```

**Exit criteria (PLAN.md M3.5):** import → wrong-parse → re-parse flow works
end-to-end in the UI. Concretely, both flows, live in one window:

1. Import a file that **fails to parse** → error card with settings → set
   `decimal ","` → Re-parse → dataset appears, columns correct.
2. Import a file that parses but **wrongly** (sentinels eating real values) →
   open its settings block → fix sentinels → Re-parse → NaN% visibly changes,
   `datasets.md` regenerated, and (auto-run on) the plot re-runs.

Plus: click-to-copy works; closing a `plot.py`-referenced file asks inline and
the next run's `KeyError` surfaces as the staleness banner; the no-`show()`
placeholder appears and clears; the M3.4 checklist's core loop still passes
after the theme pass.

**Order of work:** core (`reparse`/`remove`/`add_upload`/`dataset_head`) → hub
endpoints → worker `has_figure` → FilesPane (cards/copy/settings/close) →
placeholder + empty states → theme polish → QA leftovers → Panel deletion →
docs. The core/hub work unblocks everything; the SPA work is the bulk.

## 2. Read first (~20 min, in order)

| File | Extract |
|------|---------|
| `AGENTS.md` (repo root) | conventions, architecture rule, sample-data gotchas (**9999 is not a sentinel**) |
| `UX.md` §2, §3, §7, §9 | your spec: import settings block, click-to-copy, file actions, degradation table |
| `design.md` §4–§4.2, §6 | workspace ledger, loader guarantees (§6) |
| `PLAN.md` M3.5 + M3.4 notes | scope, exit criteria, QA leftovers |
| `src/better_mad/core/workspace.py` | `add_file`, sidecar dump/load, `_rehydrate_datasets`, `render_manifest` — you extend this |
| `src/better_mad/core/loading.py` | `ParserSettings` (frozen dataclass), `LoaderError`, `DEFAULT_SENTINELS` |
| `src/better_mad/hub/app.py` | `_parse_settings`, `import_file`, `_dataset_summary` — your hub edit points |
| `web/src/files/FilesPane.tsx` | the M3.4 basic pane you rebuild (keep its import-loop shape) |
| `web/src/api.ts` | the single source of endpoint truth — extend, don't fork |
| `scripts/m34_demo_checklist.md` | regression baseline + the two QA leftovers (Notes) |
| `HANDOFF-M3.4-spa.md` §4, §9, §11 | API catalog, traps, do/don't — all still in force |

Conventions: Python gates `uv run pytest` / `uv run ruff check .` / `uv run ty
check`; web gates `npm run typecheck` + `npm test` (in `web/`). All green before
committing; commits prefixed `M3.5:`.

## 3. Code map

```
src/better_mad/core/workspace.py   ← EDIT (main): sources_dir, add_upload,
                                      reparse, remove, dataset_head (+ tests)
src/better_mad/hub/app.py          ← EDIT: 3 endpoints + settings in summaries
                                      + import cleanup + reparse-runs
src/better_mad/worker/{executor,control}.py ← EDIT (small): has_figure
src/better_mad/core/__init__.py    ← EDIT (docstring): drop the better_mad.app
src/better_mad/cli.py              ← EDIT (docstring): W10 note now resolved
src/better_mad/app/                ← DELETE (Panel layer, dead since M3.2)
tests/test_preview.py              ← DELETE (its tests)
pyproject.toml                     ← EDIT: remove `panel>=1.5`; uv sync
web/src/api.ts                     ← EDIT: new types + helpers
web/src/files/FilesPane.tsx        ← REBUILD (the bulk of the SPA work)
web/src/files/settingsForm.ts      ← CREATE: pure settings ⇄ JSON helpers (tested)
web/src/state/embedPolicy.ts       ← EDIT: no-figure ok run ⇒ no re-embed
web/src/plot/PlotPane.tsx          ← EDIT: no-show placeholder overlay
web/src/App.tsx, web/src/styles.css ← EDIT: quick-start card, theme polish
web/src/chat/ChatPane.tsx          ← EDIT (small): reset-race fix (stretch)
```

Dev loop unchanged: hub with `--no-browser --dev-origin http://localhost:5173`,
`npm run dev` on 5173; prod via `npm run build` (hub serves `web/dist`). See
AGENTS.md commands.

## 4. API — additions to the hub catalog (M3.4 §4 stays valid)

### REST (new / changed)

| Endpoint | In | Out |
|---|---|---|
| `GET /api/files` | — | datasets: each summary **now also `settings`** (current `ParserSettings` as JSON) and **`source_exists`** (bool — false when a CLI-loaded source was moved/deleted) |
| `POST /api/files/import` | multipart `file` + optional `settings` (unchanged shape) | dataset summary \| `400 {detail}` on parse error (**temp file cleaned up on failure** — today it leaks) |
| `POST /api/files/{name}/reparse` | JSON body: same settings fields as import (all optional = defaults) | new dataset summary \| `404` unknown name \| `400` LoaderError (dataset unchanged) |
| `DELETE /api/files/{name}` | — | `{ok: true}` \| `404` unknown name |
| `GET /api/files/{name}/head?lines=20` | — | `{name, text}` — raw first-N-lines of the **source** (text, `errors="replace"`) \| `404` name \| `404 {detail: source missing}` when the file is gone |

Semantics (decided above): **reparse** re-loads from the dataset's source with
the new settings (cache key includes the settings fingerprint → always a fresh
parse; see §6.1), overwrites the snapshot + sidecar **in place** (same name),
regenerates `datasets.md`, and schedules a run iff `auto_run` is on. **close**
removes parquet + sidecar + (only if the source lives under `.data/sources/`)
the raw copy, regenerates the manifest, **never runs**. No new SSE events — the
no-dataset-event design stands (M3.4 trap T12): the pane refetches after its own
mutations.

### Run result (changed — one field)

`{status, run_seconds, stdout_tail, stderr_tail, epoch, has_figure}` —
`has_figure: true` iff `status == "ok"` **and** the run attached ≥1 figure via
`bm.show()` (worker §7.1). Flows verbatim through hub → SSE `run_result` → SPA.
Old payloads without the field: treat missing as `true` (don't flash a
placeholder at stale clients).

## 5. Verified facts (checked against the code; trust these)

- **The cache is settings-aware**: `cache_key = path + mtime + size +
  settings_fingerprint` (`core/cache.py`) — re-parse with changed settings can
  never hit a stale entry. Old entries linger in `~/.cache/better-mad/`; no
  eviction exists and none is needed for M3.5.
- **`ParserSettings` is a frozen dataclass** with fields
  `delimiter ("auto"|"whitespace"|"comma"|"tab"), decimal, sentinels
  (tuple[float|str,...]), comment, float64_columns (tuple[str,...]|None)` —
  already JSON-round-tripped by the sidecar dump/load in `workspace.py`; the
  hub's `_parse_settings` already validates the exact multipart shape (reuse its
  rules for the reparse JSON body).
- **`decimal ","` + comma delimiter raises `LoaderError`** ("ambiguous") — the
  loader guards it for you; surface the message as-is.
- **A non-numeric column is a hard `LoaderError`** (not silent NaN): with the
  wrong `decimal`, comma-decimal files fail loudly → the failed-import card flow.
  With the right settings they parse cleanly. Both exit-criteria flows work with
  the existing loader — **no loader changes**.
- **`_rehydrate_datasets` globs `.data/*.parquet` only** — a `sources/`
  subdirectory in `.data` is invisible to it; sidecars keep pointing at sources.
- **The worker's `/run` response is passed verbatim** by the hub
  (`manager.run()` → `/api/run` body, `{"type": "run_result", **result}` on SSE;
  only the `"type"` key is stripped for `last_run`) — `has_figure` needs **zero**
  hub code once the worker emits it.
- **Clipboard**: `navigator.clipboard.writeText` needs a secure context —
  localhost qualifies in dev and prod (hub on 127.0.0.1). It can still reject on
  focus loss; catch and fall back to a selected-text prompt (§6.4). jsdom has no
  real clipboard — mock it in tests.
- **Upload size is a non-issue**: Starlette spools `UploadFile` to disk past
  1 MB; the 121 MB sample imports through the browser in ~1–2 s of copy — but
  the button must show a busy state (no fetch progress in this flow).
- **`plot_changed` does not fire for dataset changes** — the watcher watches
  `plot.py` only. Correct per design; the pane refetches after mutations.

## 6. Design

### 6.1 Core — `workspace.py` (headless, pytest-covered)

- `sources_dir` property → `self.path / ".data" / "sources"` (mkdir parents on
  first use).
- **`add_upload(src: Path, original_name: str, settings: ParserSettings | None)
  -> Dataset`**: parse from `src` (LoaderError propagates; **nothing registered,
  nothing copied** — the caller deletes `src`). On success: determine the
  dataset name exactly like `add_file` (stem dedup `_2`, `_3`), copy the raw
  bytes to `sources_dir / f"{name}{suffix}"` (suffix = `original_name`'s suffix,
  may be `""` — the samples have none), then register with `path` = the persisted
  copy: parquet + sidecar + manifest, same as `add_file`. Refactor the shared
  tail into one private `_register(ds, name)` — `add_file` uses it too.
- **`reparse(name: str, settings: ParserSettings) -> Dataset`**: unknown name →
  `KeyError` (hub → 404). `load_dataset(ds.path, settings)` — LoaderError
  propagates with the dataset untouched. Overwrite the parquet **atomically**
  (`tmp + os.replace`; a concurrent worker `bm.data()` read then sees either the
  old or the new file, never a truncated one), rewrite the sidecar, replace
  `self.datasets[name]` (**name preserved** even if columns changed), refresh
  the manifest.
- **`remove(name: str) -> None`**: unknown name → `KeyError`. Delete parquet +
  sidecar; delete the raw source **only if it resolves under `sources_dir`**
  (never delete a user's CLI file — correctness trap, test it); drop the entry;
  refresh the manifest.
- **`dataset_head(name: str, lines: int = 20) -> str`**: first N lines of the
  dataset's source, text mode, `errors="replace"`; `FileNotFoundError` → the
  hub maps to 404-with-detail. (Mirror `loading._head`; a tiny private helper is
  fine, don't make it public API.)
- Manifest `Source:` rows will now show `.data/sources/...` for uploads — honest
  and expected; note it in the design.md update (§11), no masking.

### 6.2 Hub — `hub/app.py`

- `import_file`: save upload to a temp file (as today) → `ws.add_upload(temp,
  original, settings)`; on `LoaderError` → 400 `{detail}` **and delete the temp
  file** (finally-block); on success → summary (now with `settings` +
  `source_exists: True`).
- `_dataset_summary(name, ds)` gains `"settings": asdict(ds.settings)` (sentinels
  → list; it's JSON-bound, tuples listify fine) and `"source_exists":
  ds.path.exists()`.
- Three new routes per §4 (`reparse`, `close`, `head`) — all `asyncio.to_thread`
  for the file-heavy paths (121 MB re-parse ≈ 11 s cold; the hub stays
  responsive, SSE keeps heartbeating — same reasoning as M3.2's import).
- `reparse` success: if `state.auto_run`, call `state._schedule_run()` (or a
  small public wrapper) — the coalescing logic (in-flight run → `pending`) is
  already correct for this.
- Settings validation: extract `_parse_settings`'s JSON-object branch into a
  helper reused by import (multipart str) and reparse (JSON body). One shape,
  two transports.

### 6.3 Worker — `has_figure` (the M3.4 deferral, ~15 lines + test)

- `worker/executor.py`: `RunResult` gains `has_figure: bool = False`; set
  `has_figure=(status == "ok" and collector.last_plot is not None)`.
- `worker/control.py`: include `"has_figure": result.has_figure` in the `/run`
  JSON.
- Hub: nothing (verbatim passthrough, §5). SPA: `RunResult` type + §6.5.

### 6.4 SPA — FilesPane rebuild (the bulk)

Keep the M3.4 shell shape (pane → head/scroll/foot, refetch-after-own-mutations,
error banner top). Grow it:

- **Card (registered dataset)**: `name`, `rows × cols`, dimmed one-line `source`
  (truncate middle), and an action row: **⚙ Settings** (toggles the block),
  **⟳ Reload** (re-parse with *current* settings — the "reload from disk" action;
  disabled with a tooltip when `source_exists` is false), **✕ Close**.
  Expanded column table gains **min / max / NaN%** columns (data already in
  `ColumnInfo`; format ≤6 significant digits, `null` min/max as `—`).
- **Click-to-copy**: the script-name cell is a button — click copies the
  sanitized name (`navigator.clipboard.writeText`, `.catch` → select the text
  and keep it selected as the fallback), title tooltip `"original → script"`,
  transient "copied ✓" state (~1 s). This is the whole feature; no picker.
- **Settings block** (collapsible `<details>` inside the card, default closed;
  open state per card): form for delimiter (select: auto/whitespace/comma/tab),
  decimal (`.`/`,`), sentinels (free text, comma/space separated; numeric tokens
  → numbers, else strings; empty = no sentinels — **explicitly allow removing
  them all**), comment (1-char text), float64 columns (free text list, blank =
  auto). Shows **current settings** as initial values (from `/api/files`) and a
  **first-20-rows preview** (`<pre>` from `GET …/head`; for failed-import cards
  read the retained `File` client-side: `file.slice(0, 8192)` → text). Buttons:
  **[Re-parse]** (POST reparse → replace the card's dataset from the response →
  refetch list) and note that nothing re-parses silently (UX §3).
- **Failed-import cards** (session state only — a `useState` list of
  `{file: File, error: string}` in the pane): name, error text, the same
  settings form (client-side preview), **[Re-parse]** = full re-POST of the
  import (with `settings`) — success → drop the card, dataset appears; failure →
  update the card's error. **[Dismiss]** drops the card. F5 loses these cards —
  accepted (decided; don't add persistence).
- **Close confirm**: on ✕, `GET /api/plot.py` (404 → no references); referenced
  iff the text contains `bm.data("<name>")` or `bm.data('<name>')` (**exact
  quoted match** — never a bare substring, `foo` must not match `foo_2`). If
  referenced → inline confirm strip in the card: *"plot.py references this
  dataset — the next run will fail with a KeyError. [Close anyway] [Cancel]"*.
  Unreferenced → close immediately. UX §7's promised failure path needs no extra
  code: the next run's stderr already lands in the staleness banner.
- **Files pane empty state** (no datasets, no failed cards): keep the current
  hint + a subtle "＋ Add files" emphasis; the bigger first-launch moment lives
  in the center (§6.6).

`web/src/files/settingsForm.ts` — **pure, tested** helpers: `parseSentinels(str)
→ (float|str)[]`, `formToSettings(form) → settings JSON`, `settingsToForm(json)
→ form`, and `referencedInPlot(name, content) → boolean`. FilesPane itself
stays thin over these.

### 6.5 SPA — no-`show()` placeholder + runState

- `api.ts`: `RunResult` gains `has_figure: boolean` (old payloads: default true).
- `state/runState.ts`: derive `noShow: boolean` = last result ok && !has_figure.
- `state/embedPolicy.ts`: extend — an ok run with `has_figure === false` does
  **not** bump the embed token (the doc still holds the last good roots;
  re-embedding would resurrect them and fight the placeholder). Keep the pure-fn
  shape; add the case to its tests.
- `plot/PlotPane.tsx`: when `noShow`, dim the embed (CSS overlay, the iframe
  stays alive — do not tear it down) and show the placeholder text:
  *"Script ran but produced no figure."* + hint *"End the script with
  `bm.show(fig)`."* (UX §6). Any later ok run with a figure clears it via the
  normal re-embed path.

### 6.6 SPA — empty states, quick-start, theme

- **Quick-start card** (center pane): visible iff **no datasets AND no ok run**
  (both derivable from existing state; fetch `/api/files` in App or lift the
  pane's data — pick the lift, avoid a second fetch loop). Three steps: *1. Add
  files (right panel) → 2. Tell the agent what to plot (left) → 3. Zoom — it
  re-aggregates live.* Replaces the bare plot-empty text in that state only.
- **Theme polish (dark-only)**: one pass over `styles.css` — consistent focus
  states (`:focus-visible` on buttons/inputs/summary), hover affordances on the
  new card actions, banner hierarchy (error/warn/info already colored; keep),
  scrollbar styling for the three scroll areas (subtle, dark), table zebra or
  row hover in file cards, and check the one-dark CodeMirror against the app
  palette. No variable renaming churn beyond what's needed; boring CSS only.
- **QA leftovers** (from `scripts/m34_demo_checklist.md` Notes — small, do them):
  1. *Chat reset race*: after a sidecar restart, `New chat` once showed the
     stale transcript — on chat SSE reconnect, refetch history **and** health
     before re-enabling the composer buttons (the M3.4 T6 pattern applied to the
     reset path).
  2. *Hub log noise*: the chat SSE proxy logs a `RemoteProtocolError` traceback
     when a client drops mid-chunk — catch `httpx.RemoteProtocolError` (and
     `httpx.ReadError`) in the stream generator / passthrough, log one quiet
     line. Recovery already verified; this is cosmetics.

## 7. Cleanup — delete the Panel layer (decided)

- `git rm -r src/better_mad/app tests/test_preview.py`
- `pyproject.toml`: drop `"panel>=1.5"`; `uv sync` (lock shrinks — commit it).
- Docstrings that reference it: `core/__init__.py` ("The Panel app in
  ``better_mad.app``…"), `cli.py` ("stays in the tree until the cleanup step").
- Expect the pytest count to **drop** by test_preview's tests — that is correct;
  say so in the report, don't "compensate".
- Gate check: `uv run pytest` must stay green (nothing imports `better_mad.app`;
  verified at M3.2). Do this **last** so a surprise can't block the feature work.

## 8. Traps

| # | Trap | Rule |
|---|------|------|
| T1 | `navigator.clipboard` rejects without focus / in some embeds | `.catch` → fallback: select the cell text (a click-to-copy that degrades to select-to-copy); never an error banner |
| T2 | Bare-substring reference check (`foo` matches `foo_2`) | Match `bm.data("name")` / `bm.data('name')` exactly (pure fn, tested) |
| T3 | Re-parse overwriting the parquet while a worker run reads it | Write `tmp` + `os.replace` (atomic on the same filesystem); a run mid-`bm.data()` sees old-or-new, never truncated |
| T4 | `remove()` deleting a user's CLI file | Delete the raw copy **only if** it resolves under `sources_dir`; test with a CLI-style add |
| T5 | Failed-import temp files leak in `/tmp` (today's behavior) | Delete the upload temp in a `finally` on the failure path |
| T6 | Dataset name dedup (`foo`, `foo_2`) vs sources naming | Name the persisted copy from the **final dataset name** (`sources/foo_2<suffix>`); never the raw upload name |
| T7 | `has_figure` dropped or defaulted somewhere in the chain | Worker sets it; hub passes verbatim; SPA defaults missing→true (old hub in memory during upgrade) — add one fixture test per layer |
| T8 | Re-parse while a run is in flight | Nothing to do — `_schedule_run`'s pending/coalesce logic already covers it; don't add a second path |
| T9 | Import button double-click → duplicate 121 MB parses | Disable the button + label "importing…" while a POST is in flight (no fetch progress exists) |
| T10 | No dataset-changed SSE event (by design) | Refetch `/api/files` after every own mutation (import/reparse/close) — and only then; don't poll |
| T11 | Modal creep | No modal system, ever — inline confirms only (decided; CodePane precedent) |
| T12 | vitest 4 SIGILL on this AVX1 CPU | Stay on `^3.2`; no "helpful" upgrades of vite/vitest majors |
| T13 | The sample files: 9999 rows are real CMP numbers; no real sentinels exist | Don't demo sentinel-adding on `data/14_01_*` (it would eat real data — fine for a *throwaway* wrong-parse demo, never presented as a fix); craft small files in `/tmp` instead |
| T14 | `decimal ","` + comma delimiter | Loader already rejects ("ambiguous") — surface the message; the settings form may disable the combination proactively |
| T15 | Stale `web/dist` shadowing dev debugging (M3.4 T9) | Dev on 5173 with `--dev-origin`; after theme changes rebuild before prod QA |

## 9. Tests

**Python (extend):**
- `test_workspace.py`: `add_upload` (success → source under `sources/`, sidecar
  points at it; LoaderError → nothing registered/copy made; name dedup names the
  copy `foo_2…`); `reparse` (settings change → new df + sidecar + manifest,
  name preserved; LoaderError → dataset unchanged; 404 keyerror); `remove`
  (parquet+sidecar gone; sources copy deleted for uploads, **kept** for CLI
  paths); `dataset_head` (content, missing source); rehydrate still works with a
  `sources/` dir present.
- `test_hub.py`: `/api/files` carries `settings` + `source_exists`; import
  failure cleans the temp file and 400s; reparse 200/400/404 (+ schedules a run
  when `auto_run`, not when off); close 200/404 (no run scheduled); head
  200/404/missing-source.
- `test_worker.py`: ok+`show()` → `has_figure: true`; ok without `show()` →
  `false`; error → `false`.
- Suite count drops by test_preview's tests after §7 — expected.

**Web (vitest, jsdom):**
- `settingsForm`: sentinel parsing (numeric/string mix, empty), form⇄settings
  round-trip, `referencedInPlot` exact-quote matching (foo vs foo_2, both quote
  styles, no bm.data → false).
- `embedPolicy`: no-figure ok run → no re-embed; figure run → token bump; dead
  WS case still re-embeds.
- `runState`: `noShow` derivation (ok/no-figure → true; ok/figure → false;
  error → false; missing field → false).
- `FilesPane.test.tsx`: cards render min/max/NaN%; copy click → clipboard mock
  called with the script name; ✕ on a referenced dataset (mocked `/api/plot.py`)
  → confirm strip, not an immediate close; settings block shows current values
  from the files payload.
- `api.test.ts`: new fixture payloads (real captured JSONs) compile against the
  types.

**Manual (exit criteria — document in the commit message):**
- Flow 1: import a crafted whitespace file with comma decimals (e.g.
  `XCORD YCORD VEL` rows like `402345,5 6213456,7 2543,2`) → error card →
  settings `decimal ","` → Re-parse → dataset appears, columns numeric.
- Flow 2: import a small file containing `-999.25` values with default
  sentinels, then remove sentinels (or add a bogus one eating `9999` rows on a
  throwaway copy) → Re-parse → NaN%/min-max visibly change, manifest rewritten,
  auto-run re-plot.
- 121 MB sample imported **through the browser** at least once (busy state, then
  card + columns).
- Close referenced file (confirm strip) → Run → KeyError staleness banner; close
  unreferenced file → immediate.
- No-`show()` script (e.g. `print(bm.list_data())`) → placeholder; restore
  `bm.show()` → plot back.
- Quick-start card on a fresh workspace; disappears after first import+ok run.
- Re-run the M3.4 checklist §1/§2/§3 core loop after the theme pass; prod build
  served by the hub.

## 10. Do / don't

DO: keep the core additions pure (no hub imports in `core/`); reuse
`_parse_settings`' validation shape for reparse; make every settings change
explicit ([Re-parse] — nothing re-loads silently, UX §3); keep FilesPane a thin
component over pure, tested helpers; refetch after own mutations only; delete
the Panel layer as the last step with its own commit.

DON'T: add a light theme or theme switcher; add a modal system; add dataset SSE
events or polling; change loader semantics, the watcher, sidecar internals, or
worker run semantics (the one worker change is `has_figure`); persist failed
imports (decided against — session cards only); copy CLI-loaded files into
`sources/`; introduce a state/query/UI library; upgrade vitest/vite majors.

## 11. Documentation updates (same commits, per convention)

- `design.md` §4 layout + §4.2 ledger: `.data/` description gains
  `sources/` (raw upload copies; uploads' `source` points there; deleted on
  close — CLI sources never deleted). §9 hub endpoint list: reparse/close/head.
  Add the persistence decision to the §1 table (one row: "Import storage —
  raw copies under `.data/sources/`; failed imports session-scoped").
- `UX.md` §3/§7: inline close-confirm (not modal) + failed-import cards are
  session-scoped; re-parse runs when auto-run is on. Decisions table row if
  deemed material.
- `PLAN.md`: tick M3.5's exit line (✔ + one-line summary); note W9/W10 closed.
- `AGENTS.md`: "Current status" → M3.5 done (next: M4); gotchas unchanged
  unless you found a new one — then add it.

## 12. Definition of done

- [ ] Core: `add_upload`/`reparse`/`remove`/`dataset_head` + tests green
- [ ] Hub: reparse/close/head + settings & source_exists in summaries + import
      temp cleanup + tests green
- [ ] Worker: `has_figure` in `/run` + test; SPA consumes it (placeholder +
      no-re-embed policy) with tests
- [ ] FilesPane: settings block w/ preview + Re-parse, click-to-copy, close
      confirm, failed-import cards; columns show min/max/NaN%
- [ ] Exit flows 1 & 2 (§9 Manual) verified live in one window
- [ ] Quick-start card + theme pass; M3.4 core loop re-verified after it
- [ ] QA leftovers: chat reset race, proxy log noise
- [ ] Panel layer deleted (own commit); `uv.lock` updated; suite green after
- [ ] `uv run pytest` + `ruff` + `ty` + `npm run typecheck` + `npm test` green
- [ ] Docs updated (§11); PLAN.md M3.5 ticked

Report at the end: files changed, test counts (note the test_preview drop),
deviations from this document (with justification), surprises.
