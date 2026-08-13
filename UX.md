# better-mad — UX Specification

How the user interacts with each feature. Complements `design.md` (what) with the
interaction model (how).

## Decisions (resolved)

| # | Topic | Decision |
|---|-------|-----------|
| 1 | Plot workspace | **Tabs** — one tab per plot, renameable, closable. Grid overview is a stretch goal |
| 2 | File import | **Auto-load with best-guess defaults + per-file settings panel**. No blocking preview dialog. Revisit if misdetection proves common |
| 3 | File I/O direction | **Browser-native**: sessions and PNGs leave/enter via browser download/upload, not server-side paths |
| 4 | Expression naming | **Prompt for a name at commit, pre-filled** with a default (`expr_1`, `expr_2`, …) so throwaway calculations need only Enter |
| 5 | Screen real estate | Controls (plot options + all layer blocks) live in a fixed-width **right style drawer**; the center holds only the plot, which **resizes responsively** to fill the freed space. Target resolution 1920×1080 |

---

## 1. App shell

```
┌──────────────────────────────────────────────────────────────┐
│ Toolbar: [Add plot] [Save session] [Load session]            │
├─────────────┬──────────────────────────────┬─────────────────┤
│ Data sidebar│  Plot workspace (tabs)       │ Style drawer    │
│ • file 1    │  ┌────────────────────────┐  │ (plot & layer   │
│   columns…  │  │   active plot          │  │  properties)    │
│   filters…  │  │                        │  │                 │
│ • file 2    │  └────────────────────────┘  │                 │
└─────────────┴──────────────────────────────┴─────────────────┘
```

- **Left — data sidebar**: loaded files, their columns, filters, expressions.
- **Center — plot workspace**: tabbed; each tab holds one plot plus a compact inline
  toolbar (datashader toggle, PNG button, lock-limits).
- **Right — style drawer**: context panel for the current selection (plot or layer).
- Plots render live; there is no "apply"/"redraw" button anywhere in the core flows.

## 2. Loading files
Entry paths: CLI args (`better-mad file1 file2 …`) and an in-UI "Add files" picker.

- On add, the file is **parsed immediately with best-guess defaults**: delimiter
  auto-detect (whitespace/comma/tab), decimal separator `.`, default sentinel list.
- Each file gets an **Import settings panel**: detected delimiter, decimal separator,
  sentinel list, a preview table (first ~20 rows), and per-column parse status
  (ok / all-NaN / mixed).
- Editing any setting exposes a **Re-parse** button; nothing re-loads silently.
- Wrong parses must be *visible*, never silent: parse status and row/column counts are
  shown right in the panel.

## 3. Browsing data
- Expanding a file in the sidebar shows columns with **original display names** and
  quick stats: min / max / NaN%.
- This column list is the single source for all column pickers in the app.
- Every column selection anywhere is a **searchable dropdown/list** — never a plain
  `<select>` (files have 20–30 columns).

## 4. Creating a plot
1. **Add plot** → new tab.
2. **Type selector**: scatter / color scatter / polar scatter / histogram / 1D density /
   2D density / line graph.
3. **Role slots** appear per type: X, Y, Color, θ, r, Bin column… Each slot is a
   searchable column picker preceded by a **file selector** (so layers from another file
   start here).
4. Plot renders as soon as the minimum roles are filled; every subsequent change is live.
5. Controls are laid out in **grouped rows** (type/file · role slots · options) — never one
   wide row — so horizontal scrolling never appears.
6. Newly added plots are **auto-selected** (their tab becomes active).

## 5. Layers
- Each plot tab contains a **layer list**: visibility checkbox, reorder, duplicate,
  delete, and the layer's target file.
- **Add layer** = pick file → plot type → columns.
- Clicking a layer row opens its properties in the style drawer.
- Invalid compositions (design §4.2) are prevented by **disabling invalid options** in
  the UI, not by erroring after the fact.

## 6. Styling
The style drawer has two levels, switched by what the user clicked:
- **Plot properties** (clicked plot chrome): title, axis labels, axis limits, log/linear
  axes, legend (on/off, position, labels), equal aspect for map views, grid.
- **Layer properties** (clicked layer row): symbol, size, opacity, colormap, percentile
  clip (default 2–98%), explicit color min/max, log color scale, datashader toggle,
  tooltip columns (§10).

**v1 implementation (M4):** the drawer shows the active plot's collapsed *Plot options*
card plus one compact block per layer (its own collapsed *Layer style* card) — no
click-selection switching yet. That is deferred polish; the content is identical.

## 7. Comparison workflow
Three mechanisms, increasing power:
1. **Duplicate plot → swap file** (one action in the tab menu) for quick A/B.
2. **Lock limits**: per-plot toggle freezing the current xlim/ylim/color limits.
3. **Link group**: plots assigned the same group ID keep axes *and* color scales
   synchronized live — the concrete implementation of shared scales (design §4.4).

## 8. Datashader
- Per-layer toggle in layer properties; usable at any dataset size.
- A vector layer above ~100k points raises a **non-blocking warning banner** on the plot:
  "Rendering 708k points as vectors — consider Datashader [Enable]". Banners, never
  modals — the user is not interrupted.

## 9. Filtering
- Per-file **filter panel** in the data sidebar: pick column →
  - numeric column: dual-handle range slider,
  - discrete column (e.g. `STACK_WORD`): checkbox list of values.
- Active filters render as **removable chips** on the file entry **and** as a badge on
  every plot using that file ("filtered, 412k/708k rows").
- Rationale: file-level filters silently affecting distant plots is a classic foot-gun;
  visibility at the point of consumption is mandatory.
- Filters compose with AND; "Clear all" per file.

## 10. Expressions
- Each file panel has an **expression bar**: text input, function help popover
  (`log, log10, sqrt, abs, clip, where, normalize, percentile`), and clickable column
  names that insert their sanitized reference.
- **Live feedback while typing**: parse errors inline; on valid parse, a mini-preview of
  the result — row count, NaN count, small histogram — before committing.
- **Commit prompts for a column name, pre-filled** with `expr_1`, `expr_2`, …; accepting
  the default is the fast path for throwaway calculations, renaming is available later.
- Computed columns appear in the file's column list with a marker and a delete action;
  they are usable everywhere real columns are (plot roles, filters, further expressions).

## 11. Hover
- Vector layers: hover shows plotted values (x, y, color/z) by default.
- Layer properties offer a **"tooltip columns" multi-select** for extra attributes.
  Never dump the full column list (20–30 columns).
- Datashaded layers have no per-point hover; layer properties state this explicitly
  ("hover unavailable in Datashader mode") instead of leaving the user to wonder.

## 12. Selection & export (lower priority)
- Lasso/box select on vector layers → status area shows "N selected".
- **Export selection** opens a dialog: choose columns (plotted ones pre-selected, computed
  columns available), CSV via browser download.
- Selection belongs to one layer at a time in v1.

## 13. Sessions & PNG export
- All file I/O is **browser-native**:
  - Save session / export PNG → **browser downloads**.
  - Load session → **file upload widget**.
- Rationale: no server-side path assumptions; the app keeps working if ever pointed at a
  non-localhost server; matches browser user expectations.
- Loading a session with missing source files: warning banner lists the missing files;
  affected plots show a placeholder instead of crashing.

## 14. Errors & empty states
Everything degrades to banners or placeholders — never crashes, rarely modals:
- Unparsable file → error in the file's import panel, file kept for settings tweaking.
- Empty filter result → "0 rows after filtering [Clear filters]".
- NaN-only column plotted → banner on the plot.
- Session with missing files → per-plot placeholders (§13).
- **Modals only for destructive confirmations** (e.g. closing a modified plot).
- First launch / empty workspace: quick-start hints (add a file, add a plot).
