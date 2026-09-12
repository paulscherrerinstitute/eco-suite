"""eco.globallogger -- the opt-in, library-use entry point onto
eco.widgets.kernel_registry.install_shell_logger. See tests/
test_kernel_registry.py for the underlying mechanism's own tests; this
file only covers globallogger's own thin layer: label defaulting and the
no-ipython no-op."""
import pytest


class _FakeEvents:
    def register(self, name, callback):
        pass


class _FakeShell:
    def __init__(self):
        self.events = _FakeEvents()


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    from eco.widgets import kernel_registry

    monkeypatch.setattr(kernel_registry, "_registry", [])


def test_start_returns_none_with_no_ipython_session(monkeypatch):
    from eco import globallogger

    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    assert globallogger.start() is None


def test_start_labels_the_session_with_current_identity(monkeypatch, tmp_path):
    from eco import globallogger

    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    monkeypatch.setattr(
        "eco.elements.access.current_identity", lambda: type("I", (), {"name": "h_lemke"})()
    )

    session = globallogger.start(log_dir=tmp_path)

    assert session.label == "h_lemke"
    assert session.kind == "library"


def test_start_falls_back_to_plain_library_label_when_identity_unavailable(monkeypatch, tmp_path):
    from eco import globallogger

    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)

    def _boom():
        raise RuntimeError("no identity module")

    monkeypatch.setattr("eco.elements.access.current_identity", _boom)

    session = globallogger.start(log_dir=tmp_path)

    assert session.label == "library"


def test_start_honors_an_explicit_label_over_identity(monkeypatch, tmp_path):
    from eco import globallogger

    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    monkeypatch.setattr(
        "eco.elements.access.current_identity", lambda: type("I", (), {"name": "h_lemke"})()
    )

    session = globallogger.start(label="my_script", log_dir=tmp_path)

    assert session.label == "my_script"


def test_start_is_idempotent(monkeypatch, tmp_path):
    from eco import globallogger

    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)

    first = globallogger.start(log_dir=tmp_path)
    second = globallogger.start(log_dir=tmp_path)

    assert first is second
