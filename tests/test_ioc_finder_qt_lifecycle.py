"""Closing the IOC finder while a worker runs must not abort the process
(it used to destroy the running QThread with the widget)."""

import threading
import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

from eco.widgets import ioc_finder_qt


def test_closing_the_window_while_a_search_runs_does_not_abort(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    release = threading.Event()

    def slow_find(pattern):
        release.wait(5)
        return []

    monkeypatch.setattr(ioc_finder_qt, "find_ioc", slow_find, raising=False)
    finder = ioc_finder_qt.IocFinderQt()
    finder.search_box.setText("SARES20")
    finder.search()
    worker = finder._search_thread
    assert worker.isRunning()
    finder.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    finder.close()
    finder.deleteLater()
    # really delete it now (processEvents alone doesn't run deferred
    # deletions) -- with the old code this aborted the process
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)
    release.set()  # the worker finishes after its widget is gone
    assert worker.wait(5000)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)
    assert worker not in ioc_finder_qt._running_workers
