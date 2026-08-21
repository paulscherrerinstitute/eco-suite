import subprocess

import pytest

from eco.widgets.jupyter_sidecar import load_workspace, open_in_sidecar, save_workspace


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
