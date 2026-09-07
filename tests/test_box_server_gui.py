"""eco.manual_control.box_server_gui: the display/action logic, driven
directly (no real poller thread, no real HTTP server) - same testing shape
as tests/test_status_server_gui.py."""

import pytest

pytest.importorskip("qtpy")

from qtpy import QtWidgets

from eco.manual_control.box_server_gui import COLOR_BAD, COLOR_OK, COLOR_WARN, BoxServerMonitor


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    from eco.manual_control import box_server_gui as gui_module

    # Never let the real background poller touch the network during a test.
    monkeypatch.setattr(gui_module._Poller, "start", lambda self: None)
    w = BoxServerMonitor("http://fake-host:8092")
    yield w
    w._poller.stop()


def _health(**overrides):
    h = {
        "state": "connected", "connected": True, "reason": None,
        "box": "ecobox:8791", "namespace": "bernina", "pid": 12345,
        "uptime_s": 90.0, "rss_mb": 150.0, "n_threads": 8, "cpu_seconds": 5.0,
    }
    h.update(overrides)
    return h


# --------------------------------------------------------------------------
# health rendering


def test_connected_state_is_shown_in_ok_color(win):
    win._on_health(_health())
    assert "connected" in win.state_label.text()
    assert "box connected" in win.state_label.text()
    assert COLOR_OK in win.state_label.styleSheet()


def test_idle_state_shown_muted_with_no_box_connected_suffix(win):
    win._on_health(_health(state="idle", connected=False))
    assert "box connected" not in win.state_label.text()


def test_waiting_for_operator_shown_in_warn_color(win):
    win._on_health(_health(state="waiting for the operator", connected=False))
    assert COLOR_WARN in win.state_label.styleSheet()


def test_closed_reason_is_displayed(win):
    win._on_health(_health(state="closed", connected=False,
                           reason="the box was handed to lemke_h@saresc-cons-05"))
    assert "handed to" in win.reason_label.text()


def test_detail_labels_are_populated(win):
    win._on_health(_health(rss_mb=256.0, n_threads=12, pid=999))
    assert "256" in win.detail_labels["rss_mb"].text()
    assert win.detail_labels["n_threads"].text() == "12"
    assert win.detail_labels["pid"].text() == "999"


def test_health_failed_marks_the_dot_red_and_shows_the_error(win):
    win._on_health_failed("ConnectionError: refused")
    assert COLOR_BAD in win.dot.styleSheet()
    assert "refused" in win.updated_label.text()


# --------------------------------------------------------------------------
# button enablement follows connection state


def test_buttons_enabled_state_follows_connection(win):
    win._on_health(_health(connected=True))
    assert win.btn_disconnect.isEnabled()
    assert not win.btn_reconnect.isEnabled()

    win._on_health(_health(connected=False, state="idle"))
    assert not win.btn_disconnect.isEnabled()
    assert win.btn_reconnect.isEnabled()

    # restart is always available regardless of connection state
    assert win.btn_restart.isEnabled()


def test_buttons_disabled_while_an_action_is_in_flight(win):
    win._on_health(_health(connected=True))
    win._action_worker = object()  # anything non-None
    win._on_health(_health(connected=True))
    assert not win.btn_disconnect.isEnabled()
    assert not win.btn_restart.isEnabled()
    win._action_worker = None


# --------------------------------------------------------------------------
# actions: run synchronously, bypassing the worker thread


def test_run_action_reports_success(win):
    calls = []

    def fake_disconnect():
        calls.append(1)
        return {"message": "disconnected from the box"}

    win._on_action_done("disconnect", fake_disconnect())
    assert calls == [1]
    assert "disconnected" in win.action_status.text()
    assert COLOR_OK in win.action_status.styleSheet()
    assert win._action_worker is None


def test_run_action_reports_failure(win):
    win._on_action_error("restart", "ConnectionError: refused")
    assert "restart failed" in win.action_status.text()
    assert COLOR_BAD in win.action_status.styleSheet()
    assert win._action_worker is None


def test_run_action_is_a_no_op_while_one_is_already_running(win, monkeypatch):
    from eco.manual_control import box_server_gui as gui_module

    started = []
    monkeypatch.setattr(gui_module._ActionWorker, "start", lambda self: started.append(1))
    win._action_worker = None
    win._run_action("disconnect", lambda: {})
    assert started == [1]
    started.clear()
    # a second call while one is in flight must not start another worker
    win._run_action("disconnect", lambda: {})
    assert started == []
    win._action_worker = None


# --------------------------------------------------------------------------
# confirmation dialogs gate the destructive actions


def test_disconnect_requires_confirmation(win, monkeypatch):
    from eco.manual_control import box_server_gui as gui_module

    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: (asked.append(1), QtWidgets.QMessageBox.No)[1])
    ran = []
    monkeypatch.setattr(gui_module._ActionWorker, "start", lambda self: ran.append(1))

    win._confirm_disconnect()
    assert asked == [1]
    assert ran == [], "disconnect must not run without confirmation"


def test_disconnect_runs_after_confirmation(win, monkeypatch):
    from eco.manual_control import box_server_gui as gui_module

    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.Yes)
    ran = []
    monkeypatch.setattr(gui_module._ActionWorker, "start", lambda self: ran.append(1))

    win._confirm_disconnect()
    assert ran == [1]
    win._action_worker = None


def test_restart_requires_confirmation(win, monkeypatch):
    from eco.manual_control import box_server_gui as gui_module

    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.No)
    ran = []
    monkeypatch.setattr(gui_module._ActionWorker, "start", lambda self: ran.append(1))

    win._confirm_restart()
    assert ran == [], "restart must not run without confirmation"


def test_reconnect_does_not_require_confirmation(win, monkeypatch):
    """Reconnect is benign - the box operator still has to accept it - so
    it must not pop a dialog."""
    from eco.manual_control import box_server_gui as gui_module

    asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: (asked.append(1), QtWidgets.QMessageBox.Yes)[1])
    monkeypatch.setattr(gui_module._ActionWorker, "start", lambda self: None)

    win.btn_reconnect.click()
    assert asked == [], "reconnect popped a confirmation dialog"
    win._action_worker = None
