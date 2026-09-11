"""Tests for eco.widgets.subprocess_embed -- the spawn/WINID-handshake/
embed/timeout/error-fallback core extracted from
eco.widgets.camserver_panel_qt._ViewerDock/_spawn_viewer (see that
module's own tests, tests/test_camserver_panel_qt.py, for the multi-
viewer-panel-level regression coverage this extraction must keep passing
unmodified). These tests instead exercise spawn_and_embed/
EmbeddedProcessWindow directly, against a fake target (mirroring
_ViewerDock's own small protocol) or a real demo-source CLI subprocess,
same approach test_camserver_panel_qt.py already uses (--kind demo, no
cam_server/bsread/network dependency)."""
import os
import sys
import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

from eco.widgets.subprocess_embed import (
    EmbeddedProcessWindow,
    child_process_environment,
    spawn_and_embed,
)


def _pump(app, predicate, timeout=45.0, interval=0.05):
    """Process Qt events until `predicate()` is true or `timeout` elapses
    -- see tests/test_camserver_panel_qt.py's own copy of this helper for
    why the default timeout is generous (eco's known-slow first import)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


class _FakeTarget:
    """Mirrors the small protocol spawn_and_embed drives: .embedded,
    .truly_embedded, .embed(win_id), .show_floating_note(),
    .show_error(message) -- exactly what
    eco.widgets.camserver_panel_qt._ViewerDock and EmbeddedProcessWindow
    both implement for real."""

    def __init__(self):
        self.embedded = False
        self.truly_embedded = False
        self.embed_calls = []
        self.floating_note_calls = 0
        self.error_calls = []

    def embed(self, win_id):
        self.embed_calls.append(win_id)
        self.embedded = True
        self.truly_embedded = True

    def show_floating_note(self):
        self.floating_note_calls += 1
        self.embedded = True

    def show_error(self, message):
        self.error_calls.append(message)
        self.embedded = True


def test_child_process_environment_prepends_the_actual_eco_root():
    import eco

    env = child_process_environment()
    eco_root = os.path.dirname(os.path.dirname(os.path.abspath(eco.__file__)))
    pythonpath = env.value("PYTHONPATH", "")
    assert pythonpath.split(os.pathsep)[0] == eco_root


def test_spawn_and_embed_returns_a_real_qprocess():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    target = _FakeTarget()
    process = spawn_and_embed(target, [sys.executable, "-c", "pass"])
    try:
        assert isinstance(process, QtCore.QProcess)
    finally:
        _pump(app, lambda: process.state() == process.NotRunning, timeout=10.0)


def test_spawn_and_embed_calls_on_clean_finish_on_normal_exit():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    target = _FakeTarget()
    finished = {"done": False}
    spawn_and_embed(
        target, [sys.executable, "-c", "pass"],
        on_clean_finish=lambda: finished.update(done=True),
    )
    ok = _pump(app, lambda: finished["done"], timeout=15.0)
    assert ok
    assert target.error_calls == []


def test_spawn_and_embed_calls_show_error_on_nonzero_exit_before_embedding():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    target = _FakeTarget()
    spawn_and_embed(
        target,
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"],
    )
    ok = _pump(app, lambda: target.error_calls, timeout=15.0)
    assert ok
    assert "boom" in target.error_calls[-1]
    assert target.embed_calls == []
    assert target.floating_note_calls == 0


def test_spawn_and_embed_falls_back_to_floating_note_on_timeout():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    target = _FakeTarget()
    process = spawn_and_embed(
        target, [sys.executable, "-c", "import time; time.sleep(5)"], timeout_s=0.2
    )
    try:
        ok = _pump(app, lambda: target.floating_note_calls, timeout=10.0)
        assert ok
        assert target.embed_calls == []
    finally:
        process.kill()
        _pump(app, lambda: process.state() == process.NotRunning, timeout=10.0)


def test_embedded_process_window_follows_the_window_stop_convention():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = EmbeddedProcessWindow(
        [sys.executable, "-m", "eco.widgets.camserver_stream_qt", "demo", "--kind", "demo", "--embed"],
        title="test embedded viewer",
    )
    try:
        assert isinstance(window.window, QtWidgets.QWidget)
        assert window.window.windowTitle() == "test embedded viewer"
        resolved = _pump(app, lambda: window.embedded, timeout=45.0)
        assert resolved
    finally:
        window.stop()
        _pump(app, lambda: True, timeout=2.0)
        assert window.window is None
