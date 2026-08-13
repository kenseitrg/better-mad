"""Application assembly and serving (M2/M4).

Layout (UX §1): data sidebar | plot workspace (tabs) | style drawer.
The drawer holds the active plot's layer management and options so the
center is almost entirely plot (1920x1080 target).
"""

from __future__ import annotations

from pathlib import Path

import panel as pn

from better_mad.app.layers import PlotSpec
from better_mad.app.state import AppState
from better_mad.app.views import PlotTab, dataset_pane, failures_pane

#: Right style-drawer width; the plot workspace takes everything else (UX §1).
DRAWER_WIDTH = 360
SIDEBAR_WIDTH = 260


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


def build_drawer(tabs: pn.Tabs, plot_tabs: list[PlotTab]) -> pn.Column:
    """Right style drawer: always shows the active plot's settings (UX §1/§6)."""
    drawer = pn.Column(
        width=DRAWER_WIDTH,
        sizing_mode="stretch_height",
        styles={"overflow-y": "auto"},
    )

    def sync(_event: object = None) -> None:
        i = tabs.active
        if plot_tabs and i is not None and 0 <= i < len(plot_tabs):
            drawer[:] = [plot_tabs[i].settings_layout()]
        else:
            drawer[:] = [pn.pane.Markdown("*Add a plot to edit it.*")]

    # Watch both: adding the first plot keeps `active` at 0 (no event), and
    # switching tabs changes `active` without touching `objects`.
    tabs.param.watch(sync, ["active", "objects"])
    sync()
    return drawer


def build_main(tabs: pn.Tabs, drawer: pn.Column) -> pn.Row:
    """Center workspace row: stretching tabs + fixed-width style drawer."""
    return pn.Row(tabs, drawer, sizing_mode="stretch_both")


def build_template(state: AppState) -> pn.template.FastListTemplate:
    """Assemble the full UI for one session."""
    add_plot_button, tabs, plot_tabs = build_workspace(state)
    drawer = build_drawer(tabs, plot_tabs)

    sidebar: list = [add_plot_button]
    if failures := failures_pane(state):
        sidebar.append(failures)
    if state.datasets:
        sidebar.append(
            pn.Accordion(
                *((name, dataset_pane(name, state)) for name in state.datasets),
                active_header_background="#ddd",
            )
        )
    else:
        sidebar.append(pn.pane.Markdown("*No files loaded. Pass files on the command line.*"))

    if not state.datasets:
        tabs.append(("Welcome", pn.pane.Markdown("### Load files to get started")))

    return pn.template.FastListTemplate(
        title="better-mad",
        sidebar=sidebar,
        sidebar_width=SIDEBAR_WIDTH,
        main=[build_main(tabs, drawer)],
        main_layout=None,
    )


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
