import subprocess

import pytest

pytest.importorskip("qtpy")  # start_console(kind="qt") builds a real Qt window

from eco.widgets import app_launchers


def test_package_file_points_inside_the_eco_package():
    path = app_launchers._package_file("voila_app.ipynb")
    assert path.endswith("/eco/voila_app.ipynb") or path.endswith("\\eco\\voila_app.ipynb")


def test_calling_namespace_returns_none_with_no_ipython_session(monkeypatch):
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    assert app_launchers._calling_namespace() is None


def test_calling_namespace_returns_none_when_ipython_missing_attr():
    class Bare:
        pass

    import IPython

    orig = IPython.get_ipython
    try:
        IPython.get_ipython = lambda: Bare()
        assert app_launchers._calling_namespace() is None
    finally:
        IPython.get_ipython = orig


def test_calling_namespace_finds_existing_namespace_variable(monkeypatch):
    sentinel = object()

    class FakeShell:
        user_ns = {"namespace": sentinel, "other": 1}

    monkeypatch.setattr("IPython.get_ipython", lambda: FakeShell())
    assert app_launchers._calling_namespace() is sentinel


def test_calling_namespace_none_when_no_namespace_variable_set(monkeypatch):
    class FakeShell:
        user_ns = {"other": 1}

    monkeypatch.setattr("IPython.get_ipython", lambda: FakeShell())
    assert app_launchers._calling_namespace() is None


def test_find_running_jupyterlab_parses_url_from_list_output(monkeypatch):
    output = (
        "Currently running servers:\n"
        "http://localhost:8888/lab?token=abc123 :: /some/dir\n"
    )
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: output)
    url = app_launchers._find_running_jupyterlab()
    assert url == "http://localhost:8888/lab?token=abc123"


def test_find_running_jupyterlab_returns_none_when_none_running(monkeypatch):
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **kw: "Currently running servers:\n"
    )
    assert app_launchers._find_running_jupyterlab() is None


def test_find_running_jupyterlab_returns_none_when_command_unavailable(monkeypatch):
    def raise_not_found(*a, **kw):
        raise FileNotFoundError("no jupyter")

    monkeypatch.setattr(subprocess, "check_output", raise_not_found)
    assert app_launchers._find_running_jupyterlab() is None


def test_start_jupyterlab_attaches_via_browser_when_server_running(monkeypatch):
    opened = []
    monkeypatch.setattr(
        app_launchers, "_find_running_jupyterlab", lambda: "http://localhost:8888/lab?token=xyz"
    )
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(a) or object())

    result = app_launchers.start_jupyterlab(attach=True, notebook="/tmp/nb.ipynb")

    assert result is None
    assert spawned == []  # did not start a duplicate server
    assert len(opened) == 1
    assert "nb.ipynb" in opened[0]
    assert "token=xyz" in opened[0]


def test_start_jupyterlab_spawns_fresh_when_attach_false(monkeypatch):
    monkeypatch.setattr(
        app_launchers, "_find_running_jupyterlab", lambda: "http://localhost:8888/lab?token=xyz"
    )
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    class FakeProc:
        pid = 4242

    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: spawned.append(cmd) or FakeProc())

    result = app_launchers.start_jupyterlab(attach=False, notebook="/tmp/nb.ipynb")

    assert opened == []
    assert result.pid == 4242
    assert spawned == [["jupyter", "lab", "/tmp/nb.ipynb"]]


def test_start_webapp_reuses_tracked_process_when_still_running(monkeypatch):
    class FakeRunningProc:
        pid = 111

        def poll(self):
            return None  # still running

    app_launchers._voila_process = FakeRunningProc()
    try:
        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(a) or object())

        result = app_launchers.start_webapp(attach=True, notebook="/tmp/nb.ipynb")

        assert result.pid == 111
        assert spawned == []
    finally:
        app_launchers._voila_process = None


def test_start_webapp_spawns_fresh_when_no_process_tracked(monkeypatch):
    app_launchers._voila_process = None
    try:
        class FakeProc:
            pid = 222

        spawned = []
        monkeypatch.setattr(subprocess, "Popen", lambda cmd: spawned.append(cmd) or FakeProc())

        result = app_launchers.start_webapp(attach=True, notebook="/tmp/nb.ipynb", port=9999)

        assert result.pid == 222
        assert spawned == [["voila", "/tmp/nb.ipynb", "--port", "9999"]]
        assert app_launchers._voila_process is result
    finally:
        app_launchers._voila_process = None


# -- start_console --


def test_start_console_qt_dispatches_to_console_window_qt(monkeypatch):
    calls = {}

    def fake_make_console_window_qt(scope=None, lazy=True, label=None):
        calls["scope"] = scope
        calls["lazy"] = lazy
        calls["label"] = label
        return "the-window"

    monkeypatch.setattr(
        "eco.widgets.console_window_qt.make_console_window_qt", fake_make_console_window_qt
    )

    result = app_launchers.start_console(kind="qt", scope="alignment", lazy=False, label="second")

    assert result == "the-window"
    assert calls == {"scope": "alignment", "lazy": False, "label": "second"}


def test_start_console_qt_defaults_scope_to_bernina(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "eco.widgets.console_window_qt.make_console_window_qt",
        lambda scope=None, lazy=True, label=None: calls.update(scope=scope) or "w",
    )
    app_launchers.start_console(kind="qt")
    assert calls["scope"] == "bernina"


def test_start_console_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be"):
        app_launchers.start_console(kind="carrier-pigeon")


def test_fresh_console_notebook_path_is_unique_and_sanitized(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    p1 = app_launchers._fresh_console_notebook_path("my label!")
    assert p1.parent == tmp_path / ".eco" / "consoles"
    assert "my_label" in p1.name
    assert p1.suffix == ".ipynb"


def test_start_jupyterlab_console_attaches_via_browser_when_server_running(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        app_launchers, "_find_running_jupyterlab", lambda: "http://localhost:8888/lab?token=xyz"
    )
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(a) or object())

    result = app_launchers.start_console(kind="jupyterlab", scope="bernina", label="second")

    assert result is None
    assert spawned == []
    assert len(opened) == 1
    assert "console_second" in opened[0]


def test_start_jupyterlab_console_spawns_fresh_with_scope_env(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(app_launchers, "_find_running_jupyterlab", lambda: None)

    class FakeProc:
        pid = 555

    captured = {}

    def fake_popen(cmd, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = app_launchers.start_console(kind="jupyterlab", scope="alignment", lazy=False, label="two")

    assert result.pid == 555
    assert captured["cmd"][:2] == ["jupyter", "lab"]
    assert "console_two" in captured["cmd"][2]
    assert captured["env"]["ECO_SCOPE"] == "alignment"
    assert captured["env"]["ECO_LAZY"] == "0"
