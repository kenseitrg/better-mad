"""better-mad render worker (design.md §5): a programmatic bokeh server hosting
one shared live session document plus a localhost control API. Scripts execute
in-process on the worker's IOLoop; ``bm.show()`` attaches figures to the live
document, so DynamicMaps stay dynamic (zoom re-rasterizes).
"""

from better_mad.worker.executor import DEFAULT_RUN_TIMEOUT_S, FigureCollector, RunResult, run_script

__all__ = [
    "DEFAULT_RUN_TIMEOUT_S",
    "FigureCollector",
    "RunResult",
    "run_script",
]
