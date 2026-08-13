"""Application assembly and serving (M2/M4).

Layout (UX §1): data sidebar | plot workspace (tabs) | style drawer.
The drawer is the template's native right sidebar: collapsible via the header
button and drag-resizable on its left edge (JS shim below; width persisted in
localStorage). The center holds only the plot so the 1920x1080 target gets
maximum plot area.
"""

from __future__ import annotations

from pathlib import Path

import panel as pn

from better_mad.app.layers import PlotSpec
from better_mad.app.state import AppState
from better_mad.app.views import PlotTab, dataset_pane, failures_pane

#: Initial sidebar/drawer widths; both are user-resizable at runtime.
SIDEBAR_WIDTH = 300
DRAWER_WIDTH = 380

#: Drag handles + overflow guard for the two template sidebars. Injected once
#: via `pn.config.raw_css` (shared config object; guarded against re-append).
RESIZER_CSS = """
/* better-mad: resizable sidebars */
#sidebar, #right-sidebar { overflow-x: hidden; }
.bmad-resize-handle {
  position: absolute; top: 0; width: 7px; height: 100%;
  cursor: col-resize; z-index: 50; opacity: 0.18;
  background: var(--accent-fill-rest, #888);
  transition: opacity 0.15s;
}
.bmad-resize-handle:hover { opacity: 0.8; }
"""

#: Injected via `right_sidebar_footer` (raw HTML, executes on load). Adds a
#: drag handle to each sidebar's inner edge; the template layout is flexbox,
#: so the center reflows live while dragging. Widths persist in localStorage.
RESIZER_JS = """<script>
(function () {
  function apply(el, w) { el.style.minWidth = w + "px"; el.style.maxWidth = w + "px"; }
  function resizable(id, edge, min, max) {
    var sb = document.getElementById(id);
    if (!sb || sb.dataset.bmadResize) return;
    sb.dataset.bmadResize = "1";
    sb.style.position = "relative";
    var saved = parseInt(localStorage.getItem("bmad-" + id + "-width"), 10);
    if (saved >= min && saved <= max) apply(sb, saved);
    var handle = document.createElement("div");
    handle.className = "bmad-resize-handle";
    handle.style[edge] = "0";
    sb.appendChild(handle);
    handle.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      function move(ev) {
        var r = sb.getBoundingClientRect();
        var w = id === "right-sidebar" ? r.right - ev.clientX : ev.clientX - r.left;
        apply(sb, Math.min(max, Math.max(min, Math.round(w))));
      }
      function up() {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        localStorage.setItem("bmad-" + id + "-width",
          Math.round(sb.getBoundingClientRect().width));
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
  }
  function init() {
    resizable("sidebar", "right", 220, 700);
    resizable("right-sidebar", "left", 260, 800);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
</script>"""


def build_workspace(state: AppState) -> tuple[pn.widgets.Button, pn.Tabs, list[PlotTab]]:
    """The 'Add plot' button, the tabs it appends to, and the PlotTab registry."""
    tabs = pn.Tabs(sizing_mode="stretch_both")
    plot_tabs: list[PlotTab] = []

    def add_plot(spec: PlotSpec | None = None) -> PlotTab:
        index = len(tabs) + 1

        def _duplicate(dup_spec: PlotSpec) -> None:
            add_plot(spec=dup_spec)

        tab = PlotTab(state, index, spec=spec, on_duplicate=_duplicate)
        plot_tabs.append(tab)
        tabs.append((f"Plot {index}", tab.plot_layout()))
        tabs.active = index - 1  # auto-select the new tab (M3 feedback)
        return tab

    def _on_add_click(_event: object) -> None:
        add_plot()

    add_plot_button = pn.widgets.Button(label="+ Add plot", color="primary")
    add_plot_button.on_click(_on_add_click)
    return add_plot_button, tabs, plot_tabs


def wire_drawer(
    template: pn.template.FastListTemplate, tabs: pn.Tabs, plot_tabs: list[PlotTab]
) -> None:
    """Keep the template's right sidebar showing the active plot's settings."""
    drawer = template.right_sidebar
    assert drawer is not None  # always constructed with right_sidebar=[]

    def sync(_event: object = None) -> None:
        i = tabs.active
        if plot_tabs and i is not None and 0 <= i < len(plot_tabs):
            drawer[:] = [plot_tabs[i].settings_layout()]
        else:
            drawer[:] = [pn.pane.Markdown("*Add a plot to edit it.*", sizing_mode="stretch_width")]

    # Watch both: adding the first plot keeps `active` at 0 (no event), and
    # switching tabs changes `active` without touching `objects`.
    tabs.param.watch(sync, ["active", "objects"])
    sync()


def build_template(
    state: AppState,
    workspace: tuple[pn.widgets.Button, pn.Tabs, list[PlotTab]] | None = None,
) -> pn.template.FastListTemplate:
    """Assemble the full UI for one session.

    ``workspace`` lets callers (tests) inject a pre-built workspace so they can
    drive the buttons/tabs after assembly.
    """
    add_plot_button, tabs, plot_tabs = workspace or build_workspace(state)

    sidebar: list = [add_plot_button]
    if failures := failures_pane(state):
        sidebar.append(failures)
    if state.datasets:
        sidebar.append(
            pn.Accordion(
                *((name, dataset_pane(name, state)) for name in state.datasets),
                active_header_background="#ddd",
                sizing_mode="stretch_width",
            )
        )
    else:
        sidebar.append(
            pn.pane.Markdown(
                "*No files loaded. Pass files on the command line.*",
                sizing_mode="stretch_width",
            )
        )

    if not state.datasets:
        tabs.append(("Welcome", pn.pane.Markdown("### Load files to get started")))

    if not any("bmad-resize-handle" in css for css in pn.config.raw_css):
        pn.config.raw_css.append(RESIZER_CSS)

    template = pn.template.FastListTemplate(
        title="better-mad",
        sidebar=sidebar,
        sidebar_width=SIDEBAR_WIDTH,
        right_sidebar=[],
        right_sidebar_width=DRAWER_WIDTH,
        right_sidebar_footer=RESIZER_JS,
        main=[tabs],
        main_layout=None,
    )
    wire_drawer(template, tabs, plot_tabs)
    return template


def serve_app(
    files: list[str | Path],
    port: int = 5006,
    show: bool = True,
) -> None:
    """Load files, then serve the app on localhost (blocking)."""
    state = AppState()
    state.load_files(list(files))
    print("loaded datasets:")
    print(state.load_report())

    def factory() -> pn.template.FastListTemplate:
        return build_template(state)

    pn.serve({"better-mad": factory}, port=port, show=show, title="better-mad")
