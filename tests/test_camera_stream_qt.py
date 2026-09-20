"""Regression tests for a real bug: closing AxisPTZStreamQt's window via
its own native close (X) button used to leave the stream thread running
and its "Memories" sub-window's stale reference intact -- see
eco.widgets.qt_lifecycle for the underlying fix and why. Also covers the
live-video display actually being resizable (see _render_tick) -- it used
to setFixedSize the label to the raw camera frame on every frame, so
neither the image nor the window could ever be made smaller than the
camera's native resolution (reported live against cam_west, an AxisPTZ)."""
import time

import numpy as np
import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtGui, QtWidgets

from eco.widgets.camera_stream_qt import AxisPTZStreamQt


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run_event_loop(app, ms=200):
    QtCore.QTimer.singleShot(ms, app.quit)
    app.exec_()


def _pump(app, predicate, timeout=5.0, interval=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


def _fake_qimage(w, h):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    return QtGui.QImage(arr.tobytes(), w, h, w * 3, QtGui.QImage.Format_RGB888).copy()


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


def test_axisptz_stream_scales_display_to_fit_a_small_window(qapp):
    """The image itself has to actually shrink when the window/dock is
    smaller than the camera's native resolution -- previously the label
    was setFixedSize'd to the raw frame on every frame, which fought any
    attempt to resize the window smaller than that. See _render_tick,
    which redraws the last frame at the scroll area's current viewport
    size instead."""
    gui = AxisPTZStreamQt(_FakeCam(), auto_start=False)
    gui._build_window()
    try:
        gui.window.resize(400, 300)
        gui.window.show()
        gui._apply_frame((_fake_qimage(1920, 1080), 1920, 1080))
        gui._render_tick()

        assert gui._frame_size == (1920, 1080)
        assert gui._displayed_size is not None
        assert gui._displayed_size != gui._frame_size
        viewport = gui._scroll_area.viewport()
        assert gui._displayed_size[0] <= viewport.width()
        assert gui._displayed_size[1] <= viewport.height()
    finally:
        gui.stop()


def test_axisptz_click_uses_the_displayed_scale_not_the_raw_frame(qapp):
    """click_center/zoom_to_rectangle/area_zoom must be told the size of
    the coordinate space the click actually happened in (the displayed,
    possibly scaled, pixmap) -- passing the raw frame's own resolution
    there would silently mis-point the camera any time the display isn't
    shown 1:1 (i.e. essentially always, now that it's resizable)."""

    class _RecordingCam(_FakeCam):
        def __init__(self):
            self.calls = []

        def click_center(self, x, y, w, h):
            self.calls.append((x, y, w, h))

    cam = _RecordingCam()
    gui = AxisPTZStreamQt(cam, auto_start=False)
    gui._build_window()
    try:
        gui.window.resize(400, 300)
        gui.window.show()
        gui._apply_frame((_fake_qimage(1920, 1080), 1920, 1080))
        gui._render_tick()
        assert gui._displayed_size != gui._frame_size  # precondition: actually scaled

        gui._on_click(10, 20)
        assert _pump(QtWidgets.QApplication.instance(), lambda: cam.calls)

        x, y, w, h = cam.calls[0]
        assert (x, y) == (10, 20)
        assert (w, h) == gui._displayed_size
    finally:
        gui.stop()
