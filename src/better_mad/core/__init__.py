"""UI-free core: data loading, workspace model, runner, SDK, manifest generation.

Nothing in this package may import Panel, HoloViews, or any browser-facing code.
App layers (hub, worker, SPA) are thin consumers on top of this package; the
superseded Panel app layer (``better_mad.app``) was deleted in M3.5 (W10).

M0 scope: data loading (restored from the v1 ``archive/codebase`` branch).
Workspace/runner/SDK arrive in M1.
"""
