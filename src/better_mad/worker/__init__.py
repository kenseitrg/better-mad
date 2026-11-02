"""better-mad render worker (design.md §5): a programmatic bokeh server hosting
one shared live session document plus a localhost control API. Scripts execute
in-process on the worker's IOLoop; ``bm.show()`` attaches figures to the live
document, so DynamicMaps stay dynamic (zoom re-rasterizes).
"""

from better_mad.worker.control import WorkerState
from better_mad.worker.executor import DEFAULT_RUN_TIMEOUT_S, FigureCollector, RunResult, run_script
from better_mad.worker.lifecycle import READY_PREFIX, start_worker
from better_mad.worker.server import DEFAULT_SESSION_ID, WorkerHandle, boot_worker

__all__ = [
    "DEFAULT_RUN_TIMEOUT_S",
    "DEFAULT_SESSION_ID",
    "READY_PREFIX",
    "FigureCollector",
    "RunResult",
    "WorkerHandle",
    "WorkerState",
    "boot_worker",
    "run_script",
    "start_worker",
]
