"""Regression tests for a real bug: closing AxisPTZStreamQt's window via
its own native close (X) button used to leave the stream thread running
and its "Memories" sub-window's stale reference intact -- see
eco.widgets.qt_lifecycle for the underlying fix and why."""
import time

import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtWidgets

from eco.widgets.camera_stream_qt import AxisPTZStreamQt


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run_event_loop(app, ms=200):
    QtCore.QTimer.singleShot(ms, app.quit)
    app.exec_()


class _FakeCam:
    name = "cam_west"

    def iter_video_frames(self, codec=None, resolution=None, compression=None, fps=None, stop_event=None):
        # blocks until told to stop, instead of a real network stream or a
        # tight busy-loop from an immediately-empty generator
        if stop_event is not None:
            stop_event.wait(5.0)
        return
        yield  # pragma: no cover -- makes this a generator function


def test_axisptz_stream_window_sets_delete_on_close(qapp):
    gui = AxisPTZStreamQt(_FakeCam(), auto_start=False)
    gui._build_window()
    try:
        assert gui.window.testAttribute(QtCore.Qt.WA_DeleteOnClose) is True
    finally:
        gui.stop()


def test_axisptz_native_close_stops_the_stream_thread(qapp):
    gui = AxisPTZStreamQt(_FakeCam(), auto_start=False)
    gui._build_window()
    assert not gui._stop_event.is_set()

    gui.window.show()
    gui.window.close()  # simulates the native X button, not gui.stop()

    assert gui._stop_event.is_set()
    assert gui.window is None


def test_axisptz_native_close_clears_the_window_reference_via_destroyed(qapp):
    gui = AxisPTZStreamQt(_FakeCam(), auto_start=False)
    gui._build_window()
    gui.window.show()
    gui.window.close()
    _run_event_loop(qapp)  # WA_DeleteOnClose's actual deletion is deferred
    assert gui.window is None


def test_axisptz_native_close_also_closes_the_memory_browser():
    """The actual bug reported: open Memories, close it, reopen from the
    viewer -- nothing appeared, because the stale .window reference on a
    merely-hidden (not destroyed) browser window looked "still open"."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = AxisPTZStreamQt(_FakeCam(), auto_start=False)
    gui._build_window()

    class _FakeMemoryBrowser:
        def __init__(self):
            self.window = QtWidgets.QWidget()
            self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            self.window.destroyed.connect(self._on_destroyed)

        def _on_destroyed(self, *a):
            self.window = None

    fake_browser = _FakeMemoryBrowser()
    gui._memory_browser = fake_browser

    gui.window.show()
    gui.window.close()  # native close of the *viewer*, not the browser directly
    assert gui._memory_browser is None
    _run_event_loop(app)
    assert fake_browser.window is None
