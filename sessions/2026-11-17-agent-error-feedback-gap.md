# Session 2026-11-17 — M3.5 QA prep + agent error-feedback gap (M4 candidate)

## Context

Prepared the M3.5 manual-QA demo (checklist `scripts/m35_demo_checklist.md`,
running instance `/tmp/bm35/ws`). During QA the user drove the embedded agent
through a **complex plot** — cross-plot of TR_RMSAMP before vs after SCAC
(merge two 708k-row datasets on CMP, datashaded density + y=x reference line).
The task failed first; the agent fixed it only after the user pasted the app's
error banner into chat. Transcript: `/tmp/bm35/ws/.chat/2026-11-17T13-02-24-*.jsonl`.

## Timeline (transcript, UTC)

| Time | Event |
|---|---|
| 13:18:40 | User requests the cross-plot |
| 13:19:06 | Agent reads `datasets.md`, writes `plot.py` v1 |
| 13:19:09 | Agent tries to self-check: `python plot.py` via its bash tool → `RuntimeError: BETTER_MAD_DATA_DIR is not set` → concludes "the SDK only runs inside the app", downgrades to validating the *merge logic* with raw pandas (the HoloViews pipeline goes unvalidated) |
| ~13:19:30 | Worker runs v1 → **error**: `DictInterface expects tabular data` (`hv.Curve` built from a tuple list with 2 kdims). Agent is idle — has no idea; declares the script ready |
| 13:20:55 | **User manually copies the banner error into chat** — the gap this note is about |
| 13:21:15–38 | Agent reproduces with a scratch hv test, finds the Curve bug |
| 13:22:13 | Catches the **second** bug itself (cmap/colorbar opts applied to the Overlay instead of the rasterized image) by running the full pipeline locally before finalizing |
| 13:22:55 | Final script; watcher run ok (0.5 s warm); cross-plot up |

## Findings

1. **The feedback gap is real and exactly one link long**: run results reach the
   human (banner + staleness badge) but nothing delivers them back to the agent
   after its turn ends. The agent then reports success on a broken script.
2. **The agent's first instinct was to validate by running the script** — and the
   tooling pushed back (`bm.data()` demands `BETTER_MAD_DATA_DIR`, set only inside
   the worker). A standalone pre-flight is possible **today with zero code**:
   `BETTER_MAD_DATA_DIR=.data BETTER_MAD_OUTPUT=/tmp/x.pkl .venv/bin/python plot.py`
   (verified on the session's final script: 708k rows merged, exit 0, pickle
   written; both of the session's errors — element-construction DataError and
   lazy-opts errors at capture time — surface in this mode). The sidecar's bash
   already inherits the project venv (proven by the transcript's pandas paths).
3. **The agent learns within the session**: after one error paste it switched to
   validating the full pipeline locally before declaring done (bug #2 never
   reached the user). Automatic feedback would close the loop without the human.

## Options considered (decided: defer to M4 — see PLAN.md M4)

- **A. Result file + skill recipes (agent-driven)**: hub writes
  `.run/last_result.json` after every run (a "knowledge file" per the §4.2
  asymmetry — port-free, protocol-faithful); M4 `AGENTS.md` recipes teach
  (a) the standalone pre-flight above before finishing, (b) after saving
  `plot.py`, wait for the result file to update and read it; on error, fix.
  Zero coupling changes; relies on model obedience (R4 risk for small local
  models) and burns tokens while waiting.
- **B. Sidecar auto-inject (guaranteed loop)**: after a turn in which the agent
  wrote `plot.py` (sidecar sees its own tool events — no agent-traffic parsing
  by the app), the sidecar waits for the next run result (reads the same result
  file — no HTTP, no hub-port knowledge) and, on error, sends a synthetic user
  message `[better-mad] run failed: <stderr tail>` → agent takes a fix turn.
  Cap ~2 consecutive auto-fix rounds; give up silently on timeout (watchdog is
  60 s default). Needs: sidecar logic, synthetic-message convention, design.md
  §4.1/§4.2 + UX.md §5 updates. Recommended as the safety net; A remains the
  cheap in-turn first line (most errors never reach end-of-turn).
- **C. Hub pushes errors into the sidecar chat**: rejected — the hub cannot tell
  an agent edit from a user edit (filesystem-only coupling), would inject on
  the user's own breakage, and adds a coupling direction the §4.2 asymmetry
  exists to avoid. (Related: §11.10 already defers custom agent tools.)

## Other notes from this session

- Gates all green at M3.5 HEAD: 134 pytest, ruff/ty, tsc, 100 web vitest,
  19 sidecar vitest. `ruff format --check` fails on 5 files that pre-date M3.5
  (HANDOFF-M3.1-worker.md, 2 scripts, hub/sidecar.py, test_sidecar.py) —
  cosmetic cleanup candidate, not a documented gate.
- Both M3.5 exit flows re-verified over the API on a scratch instance
  (comma-decimal 400 → re-parse with `decimal:","` → 200; sentinel NaN% 33.33 →
  sentinels `[]` → 0.0 with manifest regen; head/delete/404s; sources/ copy
  deleted with its dataset).
