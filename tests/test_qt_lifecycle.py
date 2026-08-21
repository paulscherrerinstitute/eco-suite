import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtWidgets

from eco.widgets.qt_lifecycle import close_calls_stop


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run_event_loop(app, ms=150):
    QtCore.QTimer.singleShot(ms, app.quit)
    app.exec_()


def test_close_calls_stop_sets_delete_on_close(qapp):
    w = QtWidgets.QWidget()
    close_calls_stop(w, lambda: None)
    assert w.testAttribute(QtCore.Qt.WA_DeleteOnClose) is True


def test_close_calls_stop_runs_stop_fn_on_native_close(qapp):
    w = QtWidgets.QWidget()
    calls = []
    close_calls_stop(w, lambda: calls.append(True))
    w.show()
    w.close()
    assert calls == [True]


def test_close_calls_stop_is_idempotent_when_stop_fn_recloses_the_window(qapp):
    """The established convention in this codebase: .stop() itself calls
    window.close(). Without a reentrancy guard, that would recurse back
    into this same closeEvent."""
    w = QtWidgets.QWidget()
    calls = []

    def stop_fn():
        calls.append(True)
        w.close()

    close_calls_stop(w, stop_fn)
    w.show()
    w.close()
    assert calls == [True]


def test_close_calls_stop_preserves_existing_closeevent_override(qapp):
    seen = []

    class W(QtWidgets.QWidget):
        def closeEvent(self, event):
            seen.append("class")
            super().closeEvent(event)

    w = W()
    close_calls_stop(w, lambda: seen.append("stop_fn"))
    w.show()
    w.close()
    assert seen == ["stop_fn", "class"]


def test_close_calls_stop_does_not_crash_when_stop_fn_raises(qapp):
    w = QtWidgets.QWidget()

    def boom():
        raise RuntimeError("nope")

    close_calls_stop(w, boom)
    w.show()
    w.close()  # must not raise


def test_destroyed_fires_after_close_with_a_real_event_loop(qapp):
    w = QtWidgets.QWidget()
    close_calls_stop(w, lambda: None)
    destroyed = []
    w.destroyed.connect(lambda *a: destroyed.append(True))
    w.show()
    w.close()
    _run_event_loop(qapp)
    assert destroyed == [True]
