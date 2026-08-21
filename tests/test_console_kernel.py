import pytest

pytest.importorskip("qtpy")
from qtpy import QtWidgets

from eco.widgets import console_kernel, kernel_registry


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(kernel_registry, "_registry", [])
    monkeypatch.setattr(kernel_registry, "DEFAULT_LOG_DIR", tmp_path)


# -- _subprocess_env -----------------------------------------------------------
#
# Regression coverage for a real bug: a subprocess kernel spawned with a
# plain os.environ-inherited env couldn't `import eco.widgets`, even
# though the calling process (same repo checkout) had no trouble with it.
# Root cause: the calling terminal's sys.path had the repo root on it via
# IPython's %run (how eco's own startup script makes itself importable in
# the first place), not via PYTHONPATH -- a live sys.path mutation like
# that never propagates to a child process through plain environment
# inheritance. Confirmed for real against a live spawned kernel (not just
# unit-tested) while building this fix.


def test_subprocess_env_prepends_current_sys_path_to_pythonpath(monkeypatch):
    import os as _os
    import sys as _sys

    monkeypatch.setattr(_sys, "path", ["/fake/repo/root", "/usr/lib/python3.12"])
    monkeypatch.setenv("PYTHONPATH", "/existing/path")

    env = console_kernel._subprocess_env()

    expected_prefix = _os.pathsep.join(["/fake/repo/root", "/usr/lib/python3.12"])
    assert env["PYTHONPATH"] == expected_prefix + _os.pathsep + "/existing/path"


def test_subprocess_env_without_existing_pythonpath(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "path", ["/fake/repo/root"])
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = console_kernel._subprocess_env()

    assert env["PYTHONPATH"] == "/fake/repo/root"


def test_subprocess_env_preserves_other_environment_variables(monkeypatch):
    monkeypatch.setenv("SOME_OTHER_VAR", "keep-me")
    env = console_kernel._subprocess_env()
    assert env["SOME_OTHER_VAR"] == "keep-me"


# -- can_use_inprocess_kernel -------------------------------------------------


def test_can_use_inprocess_kernel_true_with_no_running_ipython(monkeypatch):
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    assert console_kernel.can_use_inprocess_kernel() is True


def test_can_use_inprocess_kernel_false_with_a_running_ipython_shell(monkeypatch):
    monkeypatch.setattr("IPython.get_ipython", lambda: object())
    assert console_kernel.can_use_inprocess_kernel() is False


# -- pure message-field extraction --------------------------------------------


def test_extract_result_fields():
    msg = {"content": {"data": {"text/plain": "42", "text/html": "<b>42</b>"}}}
    assert console_kernel.extract_result_fields(msg) == {"text": "42"}


def test_extract_result_fields_missing_data():
    assert console_kernel.extract_result_fields({"content": {}}) == {"text": None}


def test_extract_stream_fields():
    msg = {"content": {"name": "stdout", "text": "hello\n"}}
    assert console_kernel.extract_stream_fields(msg) == {"name": "stdout", "text": "hello\n"}


def test_extract_error_fields():
    msg = {"content": {"ename": "ValueError", "evalue": "bad", "traceback": ["..."]}}
    assert console_kernel.extract_error_fields(msg) == {"ename": "ValueError", "evalue": "bad"}


# -- in-process kernel (real) --------------------------------------------------


def test_build_inprocess_kernel_shares_a_given_namespace_dict(qapp, tmp_path):
    shared = {"foo": 1}
    km, kc, session = console_kernel.build_inprocess_kernel(
        kind="desktop", label="bernina", shared_user_ns=shared
    )
    try:
        assert km.kernel.shell.user_ns is shared
        assert session.kind == "desktop"
        assert session.label == "bernina"
        assert session in kernel_registry.all_sessions()
    finally:
        console_kernel.stop_kernel(km, kc, session)
    assert session not in kernel_registry.all_sessions()


def test_build_inprocess_kernel_pushes_vars_into_a_fresh_namespace(qapp):
    km, kc, session = console_kernel.build_inprocess_kernel(
        kind="desktop", label="bernina", push_vars={"namespace": "the-namespace-object"}
    )
    try:
        assert km.kernel.shell.user_ns["namespace"] == "the-namespace-object"
    finally:
        console_kernel.stop_kernel(km, kc, session)


# -- subprocess kernel (real) --------------------------------------------------


def test_build_subprocess_kernel_starts_a_real_kernel_process(qapp):
    km, kc, session = console_kernel.build_subprocess_kernel(kind="console", label="test")
    try:
        assert km.has_kernel
        assert session.pid is not None
        assert session.connection_file is not None
        assert session in kernel_registry.all_sessions()
    finally:
        console_kernel.stop_kernel(km, kc, session)
    assert session not in kernel_registry.all_sessions()


# -- build_console_widget ------------------------------------------------------
#
# Uses a fake in place of console_kernel.LoggingJupyterWidget -- see
# tests/test_desktop_app.py's _FakeConsoleWidget docstring for why a real
# RichJupyterWidget subclass isn't constructed directly in these tests.


class _FakeSession:
    def __init__(self):
        self.logged = []

    def log_input(self, code):
        self.logged.append(code)

    def log_event(self, *a, **kw):
        pass


class _FakeConsoleWidget:
    def __init__(self, session=None):
        self.session = session
        self.kernel_manager = None
        self.kernel_client = None
        self.banner = ""
        self.executed = []

    def execute(self, code):
        self.executed.append(code)
        if self.session is not None:
            self.session.log_input(code)


def test_build_console_widget_wires_manager_client_and_banner(monkeypatch, qapp):
    monkeypatch.setattr(console_kernel, "LoggingJupyterWidget", _FakeConsoleWidget)
    session = _FakeSession()
    console = console_kernel.build_console_widget(
        "manager", "client", session, banner="hello"
    )
    assert console.kernel_manager == "manager"
    assert console.kernel_client == "client"
    assert console.banner == "hello"
    assert console.session is session
    assert console.executed == []


def test_build_console_widget_runs_startup_code_through_the_widget_not_the_client(monkeypatch, qapp):
    monkeypatch.setattr(console_kernel, "LoggingJupyterWidget", _FakeConsoleWidget)
    session = _FakeSession()
    console = console_kernel.build_console_widget(
        "manager", "client", session, startup_code="namespace = 1"
    )
    assert console.executed == ["namespace = 1"]
    # logged via the widget's own execute path, same as anything else typed
    # into the console -- not by build_subprocess_kernel itself
    assert session.logged == ["namespace = 1"]


def test_stop_kernel_is_a_no_op_with_all_nones():
    console_kernel.stop_kernel(None, None, None)  # must not raise
