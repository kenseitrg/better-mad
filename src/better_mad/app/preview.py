"""Preview pane: runs plot.py and shows the result (design.md §5, UX.md §6).

The ``PreviewApp`` state machine is headless-testable: a periodic ``tick()``
implements the watcher debounce, runs happen on a worker thread, and completed
``RunResult`` objects arrive through a queue drained on the event loop — so no
Panel/bokeh threading primitives are needed and every state transition can be
exercised directly in tests.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from datetime import datetime
from typing import Any

import holoviews as hv
import panel as pn

from better_mad.core.runner import DEFAULT_TIMEOUT_S, RunResult, run_script
from better_mad.core.workspace import Workspace

DEBOUNCE_S = 0.5
POLL_MS = 250


def _fit_container_hook(plot: Any, _element: object) -> None:
    """Bokeh sizing-policy hook: make the figure fill its Panel container.

    HoloViews does not expose width_policy/height_policy as opts, so they are set
    on the bokeh figure at render time (proven approach from the v1 app). Scripts'
    own width/height opts still serve as initial hints.
    """
    fig = plot.state
    fig.width_policy = "fit"
    fig.height_policy = "fit"


def with_preview_sizing(fig: object) -> object:
    """Attach the container-fit hook to Element/Overlay figures.

    Layout/NdLayout/GridSpace are passed through unchanged for now (rare in v2
    scripts; revisit if they appear). Ensures the bokeh backend is loaded first —
    applying opts without it raises "No plotting extension is currently loaded".
    """
    if isinstance(fig, (hv.Element, hv.Overlay)):
        if "bokeh" not in hv.Store.loaded_backends():
            hv.extension("bokeh")
        return fig.opts(hooks=[_fit_container_hook])
    return fig


_PLACEHOLDER_NO_FIGURE = (
    "### Script ran but produced no figure\nFinish the script with `bm.show(fig)` (see AGENTS.md)."
)
_PLACEHOLDER_EMPTY = (
    "### No plot yet\n"
    "Tell your agent what to plot — it writes `plot.py`, this preview updates "
    "automatically. Or edit the script yourself in the Code view (M3)."
)


class PreviewApp:
    """Header controls + preview pane, driven by the script watcher."""

    def __init__(self, workspace: Workspace, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self.ws = workspace
        self.timeout = timeout

        # watcher state
        self._seen_mtime: float | None = None
        self._changed_at: float | None = None
        self._ran_mtime: float | None = None
        self._running = False
        self._results: queue.Queue[RunResult] = queue.Queue()

        # last good output
        self._last_good: tuple[object, str] | None = None  # (figure, clock stamp)

        # widgets
        self.run_button = pn.widgets.Button(
            label="▶ Run", color="primary", sizing_mode="fixed", width=90, height=32
        )
        self.run_button.on_click(self.manual_run)
        self.auto_run = pn.widgets.Checkbox(label="Auto-run", value=True)
        self.status = pn.pane.Markdown("_idle_", sizing_mode="stretch_width")
        #: Recreated per successful run (see _show_figure): mutating a pane's
        #: object in place can produce 0-height figures when the update lands
        #: while the session document is still initializing.
        self.figure_pane = pn.pane.HoloViews(None, sizing_mode="stretch_both")
        self.banner = pn.pane.Alert(
            "", alert_type="danger", visible=False, sizing_mode="stretch_width"
        )
        self._placeholder = pn.pane.Markdown(_PLACEHOLDER_EMPTY, sizing_mode="stretch_width")
        self.center = pn.Column(
            self.banner, self.figure_pane, self._placeholder, sizing_mode="stretch_both"
        )

    # --- layout helpers -----------------------------------------------------

    def header(self) -> pn.Row:
        return pn.Row(self.run_button, self.auto_run, self.status, sizing_mode="stretch_width")

    # --- watcher state machine ----------------------------------------------

    def script_mtime(self) -> float | None:
        p = self.ws.script_path
        try:
            return p.stat().st_mtime
        except OSError:
            return None

    def tick(self) -> None:
        """One polling step: drain finished runs, then debounce-trigger new ones."""
        with contextlib.suppress(queue.Empty):
            self._apply_result(self._results.get_nowait())

        mtime = self.script_mtime()
        now = time.monotonic()
        if mtime != self._seen_mtime:
            self._seen_mtime = mtime
            self._changed_at = now
            return
        if (
            self.auto_run.value
            and mtime is not None
            and not self._running
            and mtime != self._ran_mtime
            and self._changed_at is not None
            and now - self._changed_at >= DEBOUNCE_S
        ):
            self.start_run()

    def start_run(self) -> None:
        if self._running:
            return
        self._running = True
        self._ran_mtime = self.script_mtime()
        self._set_status("_running…_")
        threading.Thread(target=self._worker, daemon=True).start()

    def manual_run(self, event: object = None) -> None:
        """Button callback (event ignored); also used for the initial run."""
        self.start_run()

    def _worker(self) -> None:
        try:
            result = run_script(self.ws, timeout=self.timeout)
        except FileNotFoundError as exc:
            result = RunResult("error", None, 0.0, "", str(exc))
        self._results.put(result)

    # --- result application ---------------------------------------------------

    def _apply_result(self, result: RunResult) -> None:
        self._running = False
        stamp = datetime.now().strftime("%H:%M:%S")
        if result.status == "ok" and result.figure is not None:
            figure = with_preview_sizing(result.figure)
            self._last_good = (figure, stamp)
            self._show_figure(figure)
            self._hide_banner()
            self._set_status(f"✓ ran in {result.duration_s:.1f} s ({stamp})")
        elif result.status == "ok":
            self._placeholder.object = _PLACEHOLDER_NO_FIGURE
            self._placeholder.visible = True
            self.figure_pane.visible = False
            self._hide_banner()
            self._set_status(f"✓ ran in {result.duration_s:.1f} s — no figure")
        elif result.status == "timeout":
            self._show_failure(f"Run timed out after {self.timeout:.0f} s", result.stderr, stamp)
        else:
            self._show_failure("plot.py failed", result.stderr, stamp)

    def _show_figure(self, figure: object) -> None:
        """Swap in a fresh HoloViews pane for the figure.

        Recreating the pane (instead of setting ``.object`` on the existing one)
        guarantees the bokeh figure initializes inside a laid-out container —
        in-place updates that land during document init render at 0 height.
        """
        new_pane = pn.pane.HoloViews(figure, sizing_mode="stretch_both")
        for i, obj in enumerate(self.center.objects):
            if obj is self.figure_pane:
                self.center[i] = new_pane
                break
        self.figure_pane = new_pane
        self._placeholder.visible = False

    def _show_failure(self, headline: str, stderr: str, stamp: str) -> None:
        if self._last_good is not None:
            _, good_stamp = self._last_good
            note = f" — showing last good result ({good_stamp})"
        else:
            self.figure_pane.visible = False
            self._placeholder.object = f"### {headline}\nNo previous figure to show."
            self._placeholder.visible = True
            note = ""
        tail = stderr.strip().splitlines()[-15:]
        self.banner.object = (
            f"**⚠ {headline} ({stamp})**{note}\n\n```\n" + "\n".join(tail) + "\n```"
        )
        self.banner.visible = True
        self._set_status(f"✗ {headline.lower()} ({stamp})")

    def _hide_banner(self) -> None:
        self.banner.visible = False

    def _set_status(self, text: str) -> None:
        self.status.object = text


def make_view(workspace: Workspace) -> pn.template.VanillaTemplate:
    """One app session: PreviewApp + periodic watcher + initial run.

    Served inside a VanillaTemplate because bokeh's "fit" sizing policies need a
    definite-height container chain. FastListTemplate wraps each main item in a
    content-sized fast-card (measured: card fills #main, but its shadow-DOM slot
    stays content-sized), collapsing figures to ~40-70 px; VanillaTemplate's main
    propagates height cleanly (measured: figure fills the viewport). M3 extends
    this shell with terminal/files panels.
    """
    hv.extension("bokeh")
    app = PreviewApp(workspace)
    pn.state.add_periodic_callback(app.tick, POLL_MS)
    app.start_run()  # show whatever plot.py currently holds on open
    return pn.template.VanillaTemplate(
        title="better-mad",
        header=[app.header()],
        main=[app.center],
    )
