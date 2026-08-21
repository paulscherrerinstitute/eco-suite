import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtWidgets

import os

import eco
from eco.widgets.camserver_panel_qt import (
    CamServerPanelQt,
    _build_viewer_args,
    _child_process_environment,
    sorted_names,
)


def test_child_process_environment_prepends_the_actual_eco_root():
    env = _child_process_environment()
    eco_root = os.path.dirname(os.path.dirname(os.path.abspath(eco.__file__)))
    pythonpath = env.value("PYTHONPATH", "")
    assert pythonpath.split(os.pathsep)[0] == eco_root


def test_child_process_environment_preserves_existing_pythonpath():
    os.environ["PYTHONPATH"] = "/some/other/path"
    try:
        env = _child_process_environment()
    finally:
        del os.environ["PYTHONPATH"]
    pythonpath = env.value("PYTHONPATH", "").split(os.pathsep)
    assert "/some/other/path" in pythonpath


def test_child_process_environment_inherits_other_variables():
    os.environ["ECO_PANEL_TEST_MARKER"] = "present"
    try:
        env = _child_process_environment()
    finally:
        del os.environ["ECO_PANEL_TEST_MARKER"]
    assert env.value("ECO_PANEL_TEST_MARKER", "") == "present"


def test_sorted_names_bernina_prefixes_first():
    names = ["SATOP31-PSCA176", "SARES20-CAMS142-C3", "alain", "SLAAR21-PSEN135", "SAROP11-PPRM078"]
    result = sorted_names(names, prefixes=("SARES", "SAROP", "SLAAR"))
    assert result == [
        "SARES20-CAMS142-C3",
        "SAROP11-PPRM078",
        "SLAAR21-PSEN135",
        "SATOP31-PSCA176",
        "alain",
    ]


def test_sorted_names_no_prefix_match_is_plain_alpha_sort():
    assert sorted_names(["b", "a"], prefixes=()) == ["a", "b"]


def test_build_viewer_args_pipeline_minimal():
    assert _build_viewer_args("my_pipeline", "pipeline") == [
        "my_pipeline",
        "--kind",
        "pipeline",
        "--embed",
    ]


def test_build_viewer_args_demo_color_flag():
    args = _build_viewer_args("demo", "demo", demo_color=True)
    assert "--demo-color" in args


def test_build_viewer_args_non_demo_ignores_demo_color():
    args = _build_viewer_args("my_cam", "camera", demo_color=True)
    assert "--demo-color" not in args


def test_build_viewer_args_urls_and_rate():
    args = _build_viewer_args("cam", "camera", camera_url="http://x:1", rate=5.0)
    assert "--camera-url" in args and "http://x:1" in args
    assert "--rate" in args and "5.0" in args


def _pump(app, predicate, timeout=45.0, interval=0.05):
    """Process Qt events until `predicate()` is true or `timeout` elapses.
    Subprocess-spawn tests need a generous timeout: launching
    `python -m eco.widgets.camserver_stream_qt` pays eco's known ~10s
    first-import cost (see feedback_no_full_eco_import) before the demo
    viewer even starts producing frames."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_panel_spawns_and_isolates_demo_viewer_subprocesses():
    """The core architectural guarantee: each viewer is a separate OS
    process, so killing one can't affect another or the panel itself."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = CamServerPanelQt(auto_start=False)
    panel._build_window()
    try:
        p1 = panel.add_viewer("demo", "demo-1")
        p2 = panel.add_viewer("demo", "demo-2")

        started = _pump(
            app,
            lambda: p1.state() == p1.Running and p2.state() == p2.Running,
        )
        assert started, "both demo viewer subprocesses should have started"
        assert p1.processId() != p2.processId()

        resolved = _pump(
            app,
            lambda: panel._docks[0].embedded and panel._docks[1].embedded,
        )
        assert resolved, "both viewer docks should have embedded or fallen back within the timeout"
        assert len(panel._docks) == 2

        # simulate a crash in one viewer and confirm the other, and the
        # panel itself, are unaffected
        p1.kill()
        _pump(app, lambda: p1.state() == p1.NotRunning, timeout=10.0)
        _pump(app, lambda: True, timeout=1.0)  # let the finished-handler react

        assert p2.state() == p2.Running
        assert panel.window is not None
        # p1's dock was never *truly* embedded here -- this offscreen Qt
        # platform can't do real X11 embedding (see the "platform plugin
        # does not support foreign windows" warning), so killing it
        # surfaces an error in its dock rather than silently removing it
        # (see CamServerPanelQt._spawn_viewer.on_finished); p2 is
        # completely unaffected either way
        assert len(panel._docks) == 2
        assert panel._docks[0].process is p1
        assert panel._docks[1].process is p2
    finally:
        panel.stop()
        _pump(app, lambda: True, timeout=2.0)


def test_panel_close_button_terminates_only_that_viewer():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = CamServerPanelQt(auto_start=False)
    panel._build_window()
    try:
        p1 = panel.add_viewer("demo", "demo-a")
        p2 = panel.add_viewer("demo", "demo-b")
        _pump(app, lambda: p1.state() == p1.Running and p2.state() == p2.Running)

        dock_a = panel._docks[0]
        panel._remove_dock(dock_a)
        _pump(app, lambda: p1.state() == p1.NotRunning, timeout=10.0)

        assert len(panel._docks) == 1
        assert panel._docks[0].process is p2
        assert p2.state() == p2.Running
    finally:
        panel.stop()
        _pump(app, lambda: True, timeout=2.0)


def test_panel_shows_error_dock_when_viewer_process_exits_before_starting():
    """Reproduces the reported bug: a viewer subprocess that dies before
    ever printing a WINID (here, forced via a bad --rate value that makes
    argparse exit(2) right after eco's own slow import, well before any
    window is built) used to have its dock silently removed -- indistinguishable
    from a flash-and-vanish with no visible reason. It should now stay,
    showing the captured stderr instead."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = CamServerPanelQt(auto_start=False)
    panel._build_window()
    try:
        bad_argv = ["demo", "--kind", "demo", "--embed", "--rate", "not-a-number"]
        process = panel._spawn_viewer("broken", bad_argv)

        finished = _pump(app, lambda: process.state() == process.NotRunning, timeout=40.0)
        assert finished, "the broken viewer subprocess should have exited on its own"

        resolved = _pump(app, lambda: len(panel._docks) == 1 and panel._docks[0].embedded, timeout=5.0)
        assert resolved
        assert len(panel._docks) == 1, "the dock should stay (showing the error), not vanish"
    finally:
        panel.stop()
        _pump(app, lambda: True, timeout=2.0)
