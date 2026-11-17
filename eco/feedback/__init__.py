"""Feedback loops on eco detectors and adjustables (under development).

A PID loop per detector/adjustable pair, response-matrix measurement and
SVD matrix correction, interlocks, alarms and config save/restore -- the
functionality of the DOOCS/Java feedback server, usable

- in-process in an eco session: ``FeedbackManager`` + ``manager.qt_panel()``
  (Qt), ``manager.panel()`` (ipywidgets) or ``manager.service`` (plain
  calls), no extra dependencies;
- as an HTTP server (``manager.serve()`` in a session thread, or
  ``python -m eco.feedback serve setup.py`` as its own process) with
  ``FeedbackClient`` / ``FeedbackPanelQt(url)`` / ``FeedbackPanel(url)`` / Voila as clients. Needs
  ``fastapi`` and ``uvicorn``.

See ``eco/feedback/README.md``. Not the same thing as the older
``eco.utilities.feedback`` (a single PID Assembly used by the mono feedback).
"""

from .core import (
    PID,
    FeedbackLoop,
    FeedbackManager,
    Interlock,
    MatrixFeedbackLoop,
    ResponseMatrix,
)
from .service import FeedbackError, FeedbackService

# Only the eager names: the lazy ones below (FeedbackClient, FeedbackPanel[Qt],
# make_app, serve, serve_in_background, PersistentStore, save_config,
# load_config) would make `import *` fail where fastapi/ipywidgets are missing.
__all__ = [
    "PID", "FeedbackLoop", "FeedbackManager", "Interlock", "MatrixFeedbackLoop",
    "ResponseMatrix", "FeedbackService", "FeedbackError",
]


def __getattr__(name):
    # Lazy: fastapi/uvicorn only for the server, ipywidgets only for the panel.
    if name in ("make_app", "serve", "serve_in_background"):
        from . import server

        return getattr(server, name)
    if name in ("PersistentStore", "save_config", "load_config"):
        from . import persistence

        return getattr(persistence, name)
    if name == "FeedbackClient":
        from .client import FeedbackClient

        return FeedbackClient
    if name == "FeedbackPanelQt":
        from .gui_qt import FeedbackPanelQt

        return FeedbackPanelQt
    if name == "FeedbackPanel":
        from .gui import FeedbackPanel

        return FeedbackPanel
    raise AttributeError(name)
