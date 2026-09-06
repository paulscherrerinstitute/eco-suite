"""eco.status_server.gui: the display/state logic, driven directly (no real
poller thread, no real server) - _on_health/_on_stats/_start_reinit are
plain slots, so they can be called synchronously with fake payloads."""

import pytest

pytest.importorskip("qtpy")

from qtpy import QtGui, QtWidgets

from eco.status_server.gui import COLOR_BAD, COLOR_OK, StatusServerMonitor


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(qapp, monkeypatch):
    # Never let the real background poller touch the network during a test.
    from eco.status_server import gui as gui_module

    monkeypatch.setattr(gui_module._Poller, "start", lambda self: None)
    w = StatusServerMonitor("http://fake-host:8091")
    yield w
    w._poller.stop()


def _health(**overrides):
    h = {
        "state": "ready", "generation": 3, "uptime_s": 120.0,
        "n_initialized": 80, "n_target_names": 86, "n_failed": 6,
        "state_seconds": 5.0, "n_direct_read": 16000, "n_monitorable": 10000,
        "n_monitored": 0, "rss_mb": 2000.0, "n_threads": 100,
        "cpu_seconds": 300.0, "failed_names": [], "failed_required": [],
    }
    h.update(overrides)
    return h


# --------------------------------------------------------------------------
# health rendering


def test_ready_state_is_shown_in_ok_color(win):
    win._on_health(_health())
    assert "ready" in win.state_label.text()
    assert COLOR_OK in win.state_label.styleSheet()
    assert win.required_banner.isHidden()


def test_progress_bar_reflects_init_fraction(win):
    win._on_health(_health(n_initialized=43, n_target_names=86))
    assert win.progress.value() == 50


def test_required_failure_shows_the_red_banner(win):
    win._on_health(_health(
        state="ready", failed_names=["mon_und", "scilog"],
        failed_required=["mon_und", "scilog"],
    ))
    assert not win.required_banner.isHidden()
    assert "mon_und" in win.required_banner.text()
    assert "scilog" in win.required_banner.text()
    assert "REQUIRED" in win.required_banner.text()
    # a required failure must not also be double-listed as "other"
    assert win.other_failed_label.text() == ""


def test_non_required_failure_does_not_trigger_the_red_banner(win):
    win._on_health(_health(failed_names=["xrd"], failed_required=[]))
    assert win.required_banner.isHidden()
    assert "xrd" in win.other_failed_label.text()


def test_mixed_failures_split_correctly(win):
    win._on_health(_health(
        failed_names=["mon_und", "xrd"], failed_required=["mon_und"],
    ))
    assert "mon_und" in win.required_banner.text()
    assert "xrd" not in win.required_banner.text()
    assert "xrd" in win.other_failed_label.text()
    assert "mon_und" not in win.other_failed_label.text()


def test_eta_is_shown_while_initializing(win):
    win._on_health(_health(
        state="initializing", n_initialized=50, n_target_names=100,
        state_seconds=10.0,
    ))
    # 50% done in 10s -> another ~10s projected
    assert "eta" in win.eta_label.text()


def test_no_eta_once_ready(win):
    win._on_health(_health(state="ready"))
    assert "eta" not in win.eta_label.text()


def test_buttons_disabled_while_busy(win):
    win._on_health(_health(state="initializing"))
    assert not win.btn_failed.isEnabled()
    win._on_health(_health(state="ready"))
    assert win.btn_failed.isEnabled()


def test_unreachable_server_turns_the_dot_red_and_keeps_last_state(win):
    win._on_health(_health(state="ready"))
    win._on_health_failed("ConnectionError: refused")
    assert COLOR_BAD in win.dot.styleSheet()
    assert "ready" in win.state_label.text()  # stale, but still shown


# --------------------------------------------------------------------------
# stats table


def test_stats_summary_line(win):
    win._on_stats({"summary": {"n": 5, "n_errors": 1, "avg_duration_s": 2.5},
                   "recent": []})
    assert "5 operation" in win.summary_label.text()
    assert "1 error" in win.summary_label.text()


def test_empty_stats_says_so(win):
    win._on_stats({"summary": {"n": 0, "n_errors": 0}, "recent": []})
    assert "no requests" in win.summary_label.text()


def test_stats_table_populates_and_colors_errors(win):
    win._on_stats({
        "summary": {"n": 2, "n_errors": 1},
        "recent": [
            {"at": 1000.0, "kind": "snapshot", "duration_s": 1.2, "n_entries": 99},
            {"at": 1001.0, "kind": "capture", "duration_s": 0.5, "error": "boom"},
        ],
    })
    assert win.table.rowCount() == 2
    # most recent first
    assert win.table.item(0, 1).text() == "capture"
    assert win.table.item(0, 4).text() == "boom"
    assert win.table.item(0, 4).foreground().color().name() == QtGui.QColor(COLOR_BAD).name()
    assert win.table.item(1, 1).text() == "snapshot"
    assert win.table.item(1, 4).text() == ""


# --------------------------------------------------------------------------
# reinit action + close guard


def test_reinit_click_disables_buttons_and_calls_the_client(win, qapp):
    calls = []
    win.client.reinit = lambda **kw: calls.append(kw) or {
        "n_initialized": 80, "n_target_names": 86, "n_failed": 6,
    }
    win.btn_failed.click()
    win._reinit_worker.wait(2000)
    qapp.processEvents()
    assert calls and calls[0]["mode"] == "failed"
    assert "reinit finished" in win.action_status.text()


def test_reinit_error_is_shown(win, qapp):
    def boom(**kw):
        raise RuntimeError("server unreachable")

    win.client.reinit = boom
    win.btn_full.click()
    win._reinit_worker.wait(2000)
    qapp.processEvents()
    assert "reinit failed" in win.action_status.text()
    assert "server unreachable" in win.action_status.text()


def test_window_refuses_to_close_while_a_reinit_is_running(win, qapp):
    import threading

    release = threading.Event()
    win.client.reinit = lambda **kw: (release.wait(2), {})[1]
    win.btn_failed.click()
    qapp.processEvents()
    assert win._reinit_worker.isRunning()

    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)
    assert not ev.isAccepted()
    assert "cannot close" in win.action_status.text()

    release.set()
    win._reinit_worker.wait(2000)


def test_window_closes_normally_when_idle(win):
    ev = QtGui.QCloseEvent()
    win.closeEvent(ev)
    assert ev.isAccepted()
