"""Application assembly and serving (M2/M4)."""

from __future__ import annotations

from pathlib import Path

import panel as pn

from better_mad.app.layers import PlotSpec
from better_mad.app.state import AppState
from better_mad.app.views import PlotTab, dataset_pane, failures_pane


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
        tabs.append((f"Plot {index}", tab.layout()))
        tabs.active = index - 1  # auto-select the new tab (M3 feedback)
        return tab

    def _on_add_click(_event: object) -> None:
        add_plot()

    add_plot_button = pn.widgets.Button(label="+ Add plot", color="primary")
    add_plot_button.on_click(_on_add_click)
    return add_plot_button, tabs, plot_tabs


def build_template(state: AppState) -> pn.template.FastListTemplate:
    """Assemble the full UI for one session."""
    add_plot_button, tabs, _plot_tabs = build_workspace(state)

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
        main=[tabs],
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
