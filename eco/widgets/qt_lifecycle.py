"""One shared fix for a real bug found across nearly every top-level window
in eco's Qt widgets: none of them set `Qt.WA_DeleteOnClose`, so closing a
window via its own native close (X) button only *hides* it -- the
underlying Qt object is never actually destroyed. Two consequences, both
observed for real:

1. A `window.destroyed.connect(...)` cleanup hook (the established
   pattern throughout these widgets for clearing a stale `self.window`
   reference) never fires on a native close, only on an explicit
   `.close()` call that happens to also get garbage-collected/deleted --
   so a caller's "already open? just raise it" check (e.g.
   eco.widgets.camera_stream_qt.AxisPTZStreamQt._open_memories) keeps
   seeing a non-None `.window` and tries to raise/activate a window that
   is actually closed (hidden), doing nothing visible.
2. A background poll thread (plain `threading.Thread` + `threading.Event`,
   not a QTimer -- Qt's own child-deletion doesn't touch these) never
   gets told to stop, since nothing calls the wrapper's own `.stop()`
   when the window closes by any means other than its own explicit
   "Close" button.

`close_calls_stop` fixes both: it sets `WA_DeleteOnClose` (so the object
really is destroyed, making a `destroyed` hook meaningful) and wires
`closeEvent` to call `stop_fn` first (idempotent -- most `.stop()`
implementations in this codebase themselves call `window.close()`, which
would otherwise recurse straight back into this same closeEvent).
`closeEvent` was chosen over `destroyed` for triggering `stop_fn`
specifically because it fires synchronously, before any deletion --
reliable regardless of `WA_DeleteOnClose`, and the full widget hierarchy
is still alive to interact with (a poll thread already checks a plain
`threading.Event`, so this doesn't need to be either).
"""
import logging

from qtpy import QtCore

logger = logging.getLogger(__name__)


def close_calls_stop(window, stop_fn):
    """Wire `window` (a QWidget/QMainWindow) so that closing it -- by its
    own native close (X) button, a window manager close action, or a
    plain `.close()` call -- always runs `stop_fn` first, exactly once.
    Also sets `WA_DeleteOnClose` (see module docstring for why that's
    necessary for a `window.destroyed` hook to ever fire at all)."""
    window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

    state = {"called": False}
    original_close_event = window.closeEvent

    def _close_event(event):
        if not state["called"]:
            state["called"] = True
            try:
                stop_fn()
            except Exception:
                logger.exception("close-triggered stop() failed")
        original_close_event(event)

    window.closeEvent = _close_event
