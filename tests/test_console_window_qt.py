import pytest

pytest.importorskip("qtpy")
from qtpy import QtWidgets

from eco.widgets.console_window_qt import ConsoleWindowQt


class _FakeSession:
    def log_event(self, *a, **kw):
        pass


class _FakeConsoleWidget(QtWidgets.QWidget):
    """See tests/test_desktop_app.py's _FakeConsoleWidget for why a real
    console_kernel.LoggingJupyterWidget isn't constructed in these tests --
    a plain QWidget (unlike RichJupyterWidget) carries none of that risk,
    and ConsoleWindowQt._build_window() needs a real QWidget to hand to
    setCentralWidget()."""

    def __init__(self, session=None):
        super().__init__()
        self.session = session
        self.kernel_manager = None
        self.kernel_client = None
        self.banner = ""
        self.executed = []

    def execute(self, code):
        self.executed.append(code)


@pytest.fixture(autouse=True)
def _patch_kernel_build(monkeypatch):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls = {}

    def fake_build_subprocess(kind, label=None):
        calls["kind"] = kind
        calls["label"] = label
        return "manager", "client", _FakeSession()

    monkeypatch.setattr("eco.widgets.console_kernel.build_subprocess_kernel", fake_build_subprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.LoggingJupyterWidget", _FakeConsoleWidget)
    return calls


def test_console_window_qt_builds_a_subprocess_kernel_with_scope_startup_code(_patch_kernel_build):
    win = ConsoleWindowQt(scope="bernina", lazy=True, auto_start=False)
    win._build_window()
    try:
        assert win._kernel_manager == "manager"
        assert _patch_kernel_build["kind"] == "console"
        assert _patch_kernel_build["label"] == "bernina"
        # startup code is run through the finished console widget's own
        # .execute(), not passed to build_subprocess_kernel
        assert len(win._console.executed) == 1
        assert "build_namespace(scope='bernina', lazy=True)" in win._console.executed[0]
        assert win.window is not None
        assert "eco console: bernina" in win.window.windowTitle()
    finally:
        win.stop()


def test_console_window_qt_uses_custom_label(_patch_kernel_build):
    win = ConsoleWindowQt(scope="bernina", label="alignment", auto_start=False)
    win._build_window()
    try:
        assert _patch_kernel_build["label"] == "alignment"
        assert "eco console: alignment" in win.window.windowTitle()
    finally:
        win.stop()


def test_console_window_qt_stop_clears_window_and_kernel_refs(_patch_kernel_build):
    win = ConsoleWindowQt(scope="bernina", auto_start=False)
    win._build_window()
    win.stop()
    assert win.window is None
    assert win._kernel_manager is None
    assert win._kernel_client is None
    assert win._kernel_session is None


def test_console_window_qt_stop_before_start_is_a_no_op():
    win = ConsoleWindowQt(scope="bernina", auto_start=False)
    win.stop()  # must not raise
