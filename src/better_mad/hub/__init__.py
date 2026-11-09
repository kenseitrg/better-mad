"""better-mad hub (design §9, handoff M3.2): the FastAPI process that owns the
workspace, supervises the render worker (via
:class:`better_mad.worker.manager.WorkerManager`), runs the ``plot.py``
watcher, and serves the REST/SSE API (``/api/*``) the SPA consumes.
"""

from better_mad.hub.app import HubState, create_app
from better_mad.hub.watcher import PlotWatcher

__all__ = ["HubState", "PlotWatcher", "create_app"]
