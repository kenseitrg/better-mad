# M3.5 manual demo checklist

M3.5 = files panel & polish (PLAN M3.5, HANDOFF-M3.5-files-polish.md). Both exit
flows were already verified live-headlessly during implementation (37/37 checks,
screenshots in `/tmp/bm35/shots/`, QA script `/tmp/bm35/m35_qa.py`); this
checklist is the **manual, in-browser walkthrough** for QA + review.

The demo stack is running:

- hub + SPA (prod build): **http://127.0.0.1:5006/** — workspace `/tmp/bm35/ws`
  (**fresh/empty by design** — step 1 needs the quick-start card)
- worker: ready, epoch 1 · sidecar: ready, model `zai/glm-5.3`
- fixtures (from the implementation QA): `/tmp/bm35/flow1_commadec.txt`
  (whitespace, comma decimals), `/tmp/bm35/flow2_sentinels.txt`
  (`-999.25` sentinels); the 121 MB sample is `data/14_01_post_stack_attr_after_scac_all`
- hub log: `/tmp/bm35/hub.log`; launcher `/tmp/bm35/start.sh`

Stop everything: `pkill -f 'port 500[6]'` (bracket avoids self-match).
Restart fresh: `rm -rf /tmp/bm35/ws && setsid nohup /tmp/bm35/start.sh > /tmp/bm35/hub.log 2>&1 &`

## 1. First-launch moment (quick-start card)

1. Open **http://127.0.0.1:5006/** maximized (QA'd at 1920×1080).
2. Center pane: the **quick-start card** ("Seismic attributes → live plot in
   three steps") — not the old bare empty text. Files pane: empty hint with
   "＋ Add files" emphasis. Chat: first-run hint.
3. Theme glance: dark everywhere, no white flashes; buttons/inputs show
   `:focus-visible` rings when tabbed; file-card actions brighten on hover;
   scrollbars are dark-subtle in all three panes.

## 2. Exit flow 1 — wrong-parse → re-parse (comma decimals)

1. **＋ Add files** → pick `/tmp/bm35/flow1_commadec.txt`.
2. The card turns into a **failed-import card**: `✗ parse failed` +
   "column 'XCORD' is not numeric". No dataset appears.
3. ⚙ on the error card → settings block opens **seeded with defaults** and a
   first-20-rows preview (read client-side from the retained File).
4. Set **decimal = ","** → the Re-parse button stays enabled (note: selecting
   delimiter *comma* + decimal "," shows the "ambiguous" warning and disables
   it — loader guard, trap T14).
5. **Re-parse** → the card is replaced by a real dataset card
   (`flow1_commadec`, 50 × 3), columns float32/float64, min/max populated.
6. Sanity: `ls /tmp/bm35/ws/.data/sources/` — the raw copy persists there
   (**W9**); `datasets.md` lists the dataset.

## 3. Exit flow 2 — sentinels eaten → NaN% visibly changes

1. Import `/tmp/bm35/flow2_sentinels.txt` (defaults) — parses fine (wrongly).
2. Card table: `TR_DOMFREQ` shows **NaN% ≈ 33.3**, min/max from the real
   values only (the `-999.25` rows were eaten as default sentinels).
3. ⚙ → settings block shows **current settings** (sentinels field:
   `-999.25, -999, -9999, -99999, 1e+30, NULL`) + raw preview with the
   `-999.25` rows visible.
4. Clear the sentinels field (empty = none) → **Re-parse**.
5. Card updates in place: **NaN% → 0.0**, min now **-999.25**, settings block
   collapses; `datasets.md` regenerated with the new stats; (auto-run on) the
   plot re-runs.

## 4. Click-to-copy + stats

1. In any column row, click the **script name** cell (e.g. `tr_domfreq`) →
   "copied ✓" for ~1 s; paste somewhere → sanitized name.
2. Tooltip shows `original → script` (e.g. `TR.DOMFREQ → tr_domfreq`).
3. min/max render ≤6 significant digits; `—` for non-numeric columns.

## 5. The 121 MB browser import (busy state)

1. **＋ Add files** → `data/14_01_post_stack_attr_after_scac_all` (121 MB).
2. Button goes **"importing…" + disabled** (no double-click duplicates, T9);
   everything else stays alive (~11 s cold parse).
3. Card: `14_01_post_stack_attr_after_scac_all` (708,650 × 9) with the hostile
   names sanitized (`3DT_SEC_ORD_CELCTR` → …, `TR.DOMFREQ` → …).

## 6. Agent loop (M3.4 core regression after the theme pass)

1. Chat: `scatter XCORD_MIDPT vs YCORD_MIDPT colored by TR.DOMFREQ`
   (~15–25 s: tool notes → watcher → status ✓ → live plot).
2. **Wheel-zoom / pan** — the raster re-aggregates live.
3. Code toggle: stacked layers (canvas stays warm), CodeMirror one-dark fits
   the palette; edit the cmap → auto-run → back on Preview, changed.
4. Break it (append `raise`) → last-good plot + ⚠ staleness banner with
   **Open output**; fix → clears.

## 7. Close confirm → KeyError staleness banner

1. ✕ on the dataset the agent's `plot.py` references → **inline confirm strip**
   ("plot.py references this dataset — the next run will fail with a KeyError")
   with **[Close anyway] [Cancel]** — no modal. Cancel keeps it.
2. ✕ again → **Close anyway** → card disappears immediately; **no run fires**
   (close never runs — UX §7).
3. **▶ Run** → error banner with the `KeyError: <name>` stderr tail; last-good
   plot stays (staleness badge on).
4. ✕ on an **unreferenced** dataset (e.g. `flow1_commadec` after the agent
   switched to the big file) → closes immediately, no strip.

## 8. No-`show()` placeholder

1. Code pane: replace the tail with `print(bm.list_data())` (no `bm.show`),
   save → run ok.
2. Preview: last-good plot dims under **"Script ran but produced no figure."**
   + hint "End the script with `bm.show(fig)`." — status ✓, no error banner.
3. Restore `bm.show(fig)` → placeholder clears, plot re-embeds (fresh).

## 9. Resilience (unchanged from M3.4, quick pass)

```bash
kill -9 $(pgrep -f 'better_mad.worker --data-dir /tmp/bm35/ws')
kill -9 $(pgrep -f 'sidecar/src/main.ts --workspace /tmp/bm35/ws')
```

- Worker: banner → restart (epoch+1) → last-good replayed → re-embed.
- Sidecar: chat banner in <1.5 s, composer disabled; **New chat** after
  recovery shows a **fresh** transcript (M3.5 fix: reset race).
- F5 at any point: hello restores plot + chat history + file cards
  (failed-import cards are session-only and vanish — decided).

## 10. Wrap-up checks

- `ls /tmp/bm35/ws/.data/sources/` — raw copies of closed datasets are gone;
  the CLI never copies (this demo has none — correct).
- Hub log `/tmp/bm35/hub.log`: no `RemoteProtocolError` tracebacks when
  dropping/reloading the chat tab (M3.5 leftover 2 — one quiet info line max).
- Dev-mode variant (optional, for code-side debugging):

```bash
uv run better-mad --no-browser --workspace /tmp/bm35/ws --port 5006 \
    --dev-origin http://localhost:5173 &
cd web && npm run dev   # http://localhost:5173
```

## Notes

- Trap T13: never demo sentinel-*adding* on `data/14_01_*` (its `9999` rows
  are real CMP numbers). Fixture files only.
- Re-parse while auto-run off: no run (only the card updates) — expected.
- Reload button (⟳) disabled with tooltip when a source is missing on disk
  (not reproducible in this demo — all sources persist under `.data/sources/`).
