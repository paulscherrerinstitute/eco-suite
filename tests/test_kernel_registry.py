import json

import pytest

from eco.widgets import kernel_registry


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    # kernel_registry keeps its list of live sessions at module scope --
    # reset it around every test so tests don't see each other's sessions.
    monkeypatch.setattr(kernel_registry, "_registry", [])


def _make_session(tmp_path, **kw):
    return kernel_registry.KernelSession(log_dir=tmp_path, **kw)


def test_kernel_session_creates_log_file_with_session_start(tmp_path):
    session = _make_session(tmp_path, kind="console", label="bernina", pid=1234)

    assert session.log_path.exists()
    lines = session.log_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "session_start"
    assert entry["kind"] == "console"
    assert entry["label"] == "bernina"
    assert entry["pid"] == 1234


def test_kernel_session_defaults_label_to_kind(tmp_path):
    session = _make_session(tmp_path, kind="desktop")
    assert session.label == "desktop"


def test_log_input_and_log_output_append_jsonl_entries(tmp_path):
    session = _make_session(tmp_path, kind="desktop")
    session.log_input("namespace.motor1.get_current_value()")
    session.log_output("result", text="1.23")
    session.log_output("stream", name="stdout", text="hello\n")

    lines = [json.loads(l) for l in session.log_path.read_text().splitlines()]
    assert lines[1] == {"t": lines[1]["t"], "event": "input", "code": "namespace.motor1.get_current_value()"}
    assert lines[2]["event"] == "result"
    assert lines[2]["text"] == "1.23"
    assert lines[3]["event"] == "stream"
    assert lines[3]["name"] == "stdout"


def test_log_widget_control_appends_jsonl_entry(tmp_path):
    session = _make_session(tmp_path, kind="desktop")
    session.log_widget_control("cam_west.widget()")

    lines = [json.loads(l) for l in session.log_path.read_text().splitlines()]
    assert lines[1] == {"t": lines[1]["t"], "event": "widget_control", "code": "cam_west.widget()"}


def test_tail_returns_last_n_entries_oldest_first(tmp_path):
    session = _make_session(tmp_path, kind="desktop")
    for i in range(5):
        session.log_input(f"cmd {i}")

    tail = session.tail(n=2)
    assert [e.get("code") for e in tail] == ["cmd 3", "cmd 4"]


def test_tail_on_nonexistent_log_returns_empty(tmp_path):
    session = _make_session(tmp_path, kind="desktop")
    session.log_path.unlink()
    assert session.tail() == []


# -- install_shell_logger ------------------------------------------------
#
# Works for ANY real IPython InteractiveShell (a plain terminal `eco
# console` session, a JupyterLab-native kernel, Voila's kernel -- see the
# function's own docstring for why this needs a different mechanism than
# eco.widgets.console_kernel's ZMQ-message-based one). A fake shell with
# just enough of the real API (get_ipython(), .events.register(name, cb))
# is all that's needed to verify the wiring without a real IPython session.


class _FakeEvents:
    def __init__(self):
        self.registered = {}

    def register(self, name, callback):
        self.registered.setdefault(name, []).append(callback)

    def fire(self, name, *args):
        for cb in self.registered.get(name, []):
            cb(*args)


class _FakeShell:
    def __init__(self):
        self.events = _FakeEvents()


class _FakeCellInfo:
    def __init__(self, raw_cell):
        self.raw_cell = raw_cell


class _FakeExecutionResult:
    def __init__(self, result=None, error_in_exec=None, error_before_exec=None):
        self.result = result
        self.error_in_exec = error_in_exec
        self.error_before_exec = error_before_exec


def test_install_shell_logger_returns_none_with_no_ipython_session(monkeypatch):
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    assert kernel_registry.install_shell_logger("console") is None


def test_install_shell_logger_registers_pre_and_post_run_cell(monkeypatch, tmp_path):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)

    session = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)

    assert "pre_run_cell" in shell.events.registered
    assert "post_run_cell" in shell.events.registered
    assert shell._eco_shell_logger_session is session


def test_install_shell_logger_is_idempotent(monkeypatch, tmp_path):
    """Calling it again on the same shell (e.g. a startup script that runs
    more than once) must not double-register hooks or create a second
    session."""
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)

    first = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)
    second = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)

    assert first is second
    assert len(shell.events.registered["pre_run_cell"]) == 1
    assert len(shell.events.registered["post_run_cell"]) == 1


def test_install_shell_logger_logs_input_and_result(monkeypatch, tmp_path):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    session = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)

    shell.events.fire("pre_run_cell", _FakeCellInfo("2 + 2"))
    shell.events.fire("post_run_cell", _FakeExecutionResult(result=4))

    lines = [json.loads(l) for l in session.log_path.read_text().splitlines()]
    assert lines[1] == {"t": lines[1]["t"], "event": "input", "code": "2 + 2"}
    assert lines[2] == {"t": lines[2]["t"], "event": "result", "text": "4"}


def test_install_shell_logger_does_not_log_a_result_for_a_bare_statement(monkeypatch, tmp_path):
    """print(...)/assignments don't produce a result.result -- must not
    log a spurious empty "result" entry for those."""
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    session = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)

    shell.events.fire("pre_run_cell", _FakeCellInfo('print("hi")'))
    shell.events.fire("post_run_cell", _FakeExecutionResult(result=None))

    lines = [json.loads(l) for l in session.log_path.read_text().splitlines()]
    assert [l["event"] for l in lines] == ["session_start", "input"]


def test_install_shell_logger_logs_errors(monkeypatch, tmp_path):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    session = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)

    shell.events.fire("pre_run_cell", _FakeCellInfo("1 / 0"))
    shell.events.fire("post_run_cell", _FakeExecutionResult(error_in_exec=ZeroDivisionError("division by zero")))

    lines = [json.loads(l) for l in session.log_path.read_text().splitlines()]
    assert lines[2] == {
        "t": lines[2]["t"], "event": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
    }


def test_install_shell_logger_ignores_blank_cells(monkeypatch, tmp_path):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    session = kernel_registry.install_shell_logger("console", label="bernina", log_dir=tmp_path)

    shell.events.fire("pre_run_cell", _FakeCellInfo("   \n  "))

    lines = session.log_path.read_text().splitlines()
    assert len(lines) == 1  # just session_start -- no spurious blank input logged


def test_register_and_unregister_control_all_sessions(tmp_path):
    s1 = _make_session(tmp_path, kind="desktop")
    s2 = _make_session(tmp_path, kind="console")

    kernel_registry.register(s1)
    kernel_registry.register(s2)
    assert kernel_registry.all_sessions() == [s1, s2]

    kernel_registry.unregister(s1)
    assert kernel_registry.all_sessions() == [s2]


def test_sessions_summary_shape(tmp_path):
    session = _make_session(tmp_path, kind="console", label="alignment", pid=99)
    kernel_registry.register(session)

    [summary] = kernel_registry.sessions_summary()
    assert summary["id"] == session.id
    assert summary["kind"] == "console"
    assert summary["label"] == "alignment"
    assert summary["pid"] == 99
    assert summary["log_path"] == str(session.log_path)


def test_find_all_logs_lists_jsonl_files(tmp_path):
    _make_session(tmp_path, kind="desktop")
    _make_session(tmp_path, kind="console")
    logs = kernel_registry.find_all_logs(log_dir=tmp_path)
    assert len(logs) == 2
    assert all(p.suffix == ".jsonl" for p in logs)


def test_find_all_logs_missing_dir_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert kernel_registry.find_all_logs(log_dir=missing) == []
