"""Tests for eco.utilities.mainthread's heartbeat: work registered for the
main thread keeps running while the main thread is busy in user code, Ctrl-C
still reaches that code, and eco's untimed waits let it run."""

import contextlib
import os
import signal
import threading
import time

import pytest

from eco.utilities import mainthread
from eco.utilities.utilities import PropagatingThread


@contextlib.contextmanager
def installed(scope="always", **kwargs):
    # no IPython shell under pytest: "always" instead of the default "cells"
    mainthread.uninstall()
    heartbeat = mainthread.install(scope=scope, **kwargs)
    assert heartbeat is not None
    try:
        yield heartbeat
    finally:
        mainthread.uninstall()


def busy(seconds):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        pass


def test_call_soon_runs_on_main_thread_while_main_thread_is_busy():
    with installed():
        result = {}

        def worker():
            t0 = time.monotonic()
            future = mainthread.call_soon(
                lambda: threading.current_thread() is threading.main_thread()
            )
            result["on_main"] = future.result(timeout=5)
            result["latency"] = time.monotonic() - t0

        thread = threading.Thread(target=worker)
        thread.start()
        busy(0.6)
        thread.join(timeout=5)
        assert result["on_main"] is True
        assert result["latency"] < 0.4


class _FakeEvents:
    def __init__(self):
        self.callbacks = {}

    def register(self, event, callback):
        self.callbacks.setdefault(event, []).append(callback)

    def unregister(self, event, callback):
        self.callbacks[event].remove(callback)

    def trigger(self, event):
        for callback in self.callbacks.get(event, []):
            callback()


class _FakeShell:
    def __init__(self):
        self.events = _FakeEvents()


def test_scope_cells_only_interrupts_while_a_watched_shell_runs_a_cell():
    # the gate matters once there is Qt to re-enter (without any, calls and
    # services may interrupt outside cells too)
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with installed(scope="cells"):
        mainthread.install_qt()
        shell = _FakeShell()
        mainthread.watch_shell(shell)
        calls = []
        mainthread.add_service("count", lambda: calls.append(1), interval=0.02)
        time.sleep(0.3)
        assert calls == []  # not in a cell: the main thread is left alone
        shell.events.trigger("pre_run_cell")
        time.sleep(0.3)
        assert len(calls) >= 5
        shell.events.trigger("post_run_cell")
        n = len(calls)
        time.sleep(0.3)
        assert len(calls) == n
    assert shell.events.callbacks == {"pre_run_cell": [], "post_run_cell": []}
    del app


def test_timer_only_runs_while_needed():
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with installed(scope="cells"):
        mainthread.install_qt()
        shell = _FakeShell()
        mainthread.watch_shell(shell)
        assert mainthread.status()["timer_running"] is False  # idle: nothing in Qt's loop
        shell.events.trigger("pre_run_cell")
        assert mainthread.status()["timer_running"] is True
        shell.events.trigger("post_run_cell")
        assert mainthread.status()["timer_running"] is False
    del app


def test_call_soon_from_a_thread_runs_in_the_idle_qt_loop():
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    from qtpy import QtCore

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with installed(scope="cells"):
        mainthread.install_qt()
        result = {}
        thread = threading.Thread(
            target=lambda: result.update(on_main=mainthread.call_soon(
                lambda: threading.current_thread() is threading.main_thread()
            ).result(timeout=5))
        )
        thread.start()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(500, loop.quit)
        loop.exec_()  # idle in Qt's own loop, not in a cell: woken by the queued signal
        thread.join(timeout=5)
        assert result["on_main"] is True
    del app


def test_run_on_main_from_the_main_thread_calls_directly():
    assert mainthread.run_on_main(lambda x: x + 1, 1) == 2


def test_services_run_during_sleep_and_a_failing_service_is_removed():
    with installed():
        calls = []
        mainthread.add_service("good", lambda: calls.append(1), interval=0.05)
        mainthread.add_service("bad", lambda: 1 / 0)
        time.sleep(0.6)
        status = mainthread.status()
        assert len(calls) >= 5
        assert status["services"] == ["good"]
        assert status["last_error"] == "bad"


def test_ctrl_c_during_a_tick_is_raised_in_the_interrupted_code():
    with installed() as heartbeat:
        mainthread.add_service("sigint", lambda: os.kill(os.getpid(), signal.SIGINT))
        with pytest.raises(KeyboardInterrupt):
            time.sleep(2)
        mainthread.remove_service("sigint")
        assert heartbeat.stats["deferred_interrupts"] >= 1


def test_untimed_propagating_thread_join_lets_ticks_run_and_still_propagates():
    with installed():
        calls = []
        mainthread.add_service("count", lambda: calls.append(1), interval=0.05)
        waiter = PropagatingThread(target=time.sleep, args=(0.5,))
        waiter.start()
        waiter.join()
        assert len(calls) >= 4

        def fail():
            time.sleep(0.1)
            raise ValueError("boom")

        failing = PropagatingThread(target=fail)
        failing.start()
        with pytest.raises(ValueError):
            failing.join()


def test_wait_event_untimed_lets_ticks_run():
    with installed():
        calls = []
        mainthread.add_service("count", lambda: calls.append(1), interval=0.05)
        event = threading.Event()
        threading.Timer(0.5, event.set).start()
        assert mainthread.wait_event(event) is True
        assert len(calls) >= 4


def test_hold_suspends_ticks():
    with installed():
        calls = []
        mainthread.add_service("count", lambda: calls.append(1), interval=0.02)
        with mainthread.hold():
            time.sleep(0.3)
            assert calls == []
        time.sleep(0.3)
        assert calls


def test_disabled_by_environment_variable():
    mainthread.uninstall()
    os.environ["ECO_NO_HEARTBEAT"] = "1"
    try:
        assert mainthread.install() is None
        assert mainthread.status() == {"installed": False}
    finally:
        del os.environ["ECO_NO_HEARTBEAT"]


def test_uninstall_restores_the_signal_handler():
    with installed():
        assert signal.getsignal(signal.SIGUSR2) not in (signal.SIG_DFL, None)
    assert signal.getsignal(signal.SIGUSR2) == signal.SIG_DFL


def test_qt_events_are_processed_while_the_main_thread_sleeps():
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    from qtpy import QtCore

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with installed():
        mainthread.install_qt()
        fired = []
        timer = QtCore.QTimer()
        timer.setInterval(20)
        timer.timeout.connect(lambda: fired.append(time.monotonic()))
        timer.start()
        try:
            time.sleep(1.0)
        finally:
            timer.stop()
        assert len(fired) >= 15  # nominally 50; frozen without the heartbeat
    del app


def test_on_main_runs_gui_code_on_the_main_thread_from_a_worker():
    with installed():
        seen = []

        @mainthread.on_main
        def draw(x):
            seen.append(threading.current_thread() is threading.main_thread())
            return x * 2

        result = {}
        worker = threading.Thread(target=lambda: result.update(value=draw(21)))
        worker.start()
        busy(0.5)
        worker.join(5)
        assert result == {"value": 42}
        assert seen == [True]
        assert draw(1) == 2  # on the main thread: a plain call
