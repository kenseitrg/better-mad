"""UI-free core: data loading, workspace model, runner, SDK, manifest generation.

Nothing in this package may import Panel, HoloViews, or any browser-facing code.
The Panel app in ``better_mad.app`` is a thin layer on top of this package.

M0 scope: data loading (restored from the v1 ``archive/codebase`` branch).
Workspace/runner/SDK arrive in M1.
"""
