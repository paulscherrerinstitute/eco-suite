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
