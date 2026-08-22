import subprocess

import pytest

from eco.widgets.jupyter_sidecar import (
    load_workspace,
    open_html_in_sidecar,
    open_in_sidecar,
    open_namespace_dashboard,
    save_workspace,
)


def test_open_in_sidecar_missing_dependency_message(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sidecar":
            raise ImportError("no module named sidecar")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="pip install sidecar"):
        open_in_sidecar(object(), title="test")


def test_save_workspace_runs_export_and_writes_file(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, stdout=None, check=None):
        calls.append(cmd)
        stdout.write(b'{"fake": "workspace"}')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    path = tmp_path / "ws.json"
    result = save_workspace(path)

    assert result == path
    assert calls == [["jupyter", "lab", "workspaces", "export"]]
    assert path.read_bytes() == b'{"fake": "workspace"}'


def test_save_workspace_missing_jupyter_raises_clear_error(tmp_path, monkeypatch):
    def fake_run(*a, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="'jupyter' not found"):
        save_workspace(tmp_path / "ws.json")


def test_load_workspace_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_workspace("/no/such/path/ws.json")


def test_load_workspace_runs_import(tmp_path, monkeypatch):
    path = tmp_path / "ws.json"
    path.write_text("{}")
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, check=None: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0)
    )

    result = load_workspace(path)

    assert result == path
    assert calls == [["jupyter", "lab", "workspaces", "import", str(path)]]


def test_open_html_in_sidecar_missing_dependency_message(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sidecar":
            raise ImportError("no module named sidecar")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="pip install sidecar"):
        open_html_in_sidecar("<b>hi</b>", title="test")


class _FakeLogItem:
    def widget(self):
        import ipywidgets as widgets

        return widgets.HTML("fake widget")


class _FakeLogNamespace:
    def __init__(self):
        self.initialized_names = {"cam_west"}
        self.lazy_names = set()
        self.failed_names = set()
        self._items = {"cam_west": _FakeLogItem()}
        self._required = set()

    def resolve_item(self, name):
        return self._items.get(name)

    def required_names(self, value=None):
        if value is None:
            return sorted(self._required)
        self._required = set(value)


def test_namespace_dashboard_log_viewer_opens_via_eco_logs_sidecar(monkeypatch):
    """The JupyterLab counterpart to eco desktop's Tools -> Log Viewer menu
    action: clicking the dashboard's Log Viewer button must go through
    eco.logs.widget(prefer="sidecar") (not some parallel, duplicated
    rendering path) so it stays consistent with every other logs.widget()
    caller."""
    import eco.logs

    calls = []
    fake_sidecar = object()
    monkeypatch.setattr(eco.logs, "widget", lambda prefer: calls.append(prefer) or fake_sidecar)
    # A real ipykernel-backed InteractiveShell.instance() left behind by an
    # earlier, unrelated test in this same pytest process (a real,
    # process-wide IPython singleton) crashes IPython.display.display()'s
    # attempt to actually send display-data through it -- irrelevant to
    # what this test verifies (that opening the dashboard's log viewer
    # goes through eco.logs.widget), so short-circuit it.
    monkeypatch.setattr("IPython.display.display", lambda *a, **kw: None)

    dashboard = open_namespace_dashboard(_FakeLogNamespace())
    try:
        dashboard._open_log_viewer()
        assert calls == ["sidecar"]
        assert dashboard._log_viewer is fake_sidecar
    finally:
        dashboard.close()


def test_namespace_dashboard_log_viewer_reopen_replaces_not_piles_up(monkeypatch):
    import eco.logs

    class _FakeSidecarPanel:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    made = []

    def fake_widget(prefer):
        panel = _FakeSidecarPanel()
        made.append(panel)
        return panel

    monkeypatch.setattr(eco.logs, "widget", fake_widget)
    monkeypatch.setattr("IPython.display.display", lambda *a, **kw: None)  # see the test above

    dashboard = open_namespace_dashboard(_FakeLogNamespace())
    try:
        dashboard._open_log_viewer()
        first = dashboard._log_viewer
        dashboard._open_log_viewer()
        assert dashboard._log_viewer is made[1]
        assert dashboard._log_viewer is not first
        assert first.close_calls == 1
    finally:
        dashboard.close()


def test_namespace_dashboard_close_tears_down_log_viewer_too(monkeypatch):
    import eco.logs

    class _FakeSidecarPanel:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    monkeypatch.setattr(eco.logs, "widget", lambda prefer: _FakeSidecarPanel())
    monkeypatch.setattr("IPython.display.display", lambda *a, **kw: None)  # see the first test above

    dashboard = open_namespace_dashboard(_FakeLogNamespace())
    dashboard._open_log_viewer()
    panel = dashboard._log_viewer

    dashboard.close()

    assert panel.close_calls == 1
    assert dashboard._log_viewer is None
