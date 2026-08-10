"""Application assembly and serving (M2)."""

from __future__ import annotations

from pathlib import Path

import panel as pn

from better_mad.app.state import AppState
from better_mad.app.views import PlotTab, dataset_pane, failures_pane


def build_template(state: AppState) -> pn.template.FastListTemplate:
    """Assemble the full UI for one session."""
    tabs = pn.Tabs(sizing_mode="stretch_both")

    def add_plot(_event: object = None) -> None:
        index = len(tabs) + 1
        tab = PlotTab(state, index)
        tabs.append((f"Plot {index}", tab.layout()))

    add_plot_button = pn.widgets.Button(label="+ Add plot", button_type="primary")
    add_plot_button.on_click(add_plot)

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
