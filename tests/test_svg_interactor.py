"""Coverage for eco.utilities.svg_interactor -- previously untested. Added
alongside the dock_in (embed in eco desktop) / sidecar_anchor (embed in a
JupyterLab Sidecar panel) options: verifies both actually route to the
right embedding mechanism, without needing a real Qt WebEngine build or a
real JupyterLab session (neither is available in this environment -- see
test_launch_svg_viewer_window_backend_reports_missing_webengine_clearly, which
documents a real gap found while building this: qtpy in the deployed
bpy312 env picks PyQt5 by default, which has no QtWebEngineWidgets
installed, even though PySide6 -- also installed -- does).
"""
import sys
import types

import pytest

from eco.utilities import svg_interactor


def _make_shell(class_name, **attrs):
    """A minimal fake IPython shell -- svg_interactor only ever checks
    ip.__class__.__name__ (see _in_notebook) and a couple of GUI-loop
    attributes/methods, never anything real-IPython-specific."""
    cls = type(class_name, (), {})
    shell = cls()
    for k, v in attrs.items():
        setattr(shell, k, v)
    return shell


# -- _SvgViewerHandle ---------------------------------------------------
#
# The .window/.stop() wrapper _build_qt_window hands to
# EcoDesktopApp._dock_widget_object when dock_in is given -- same
# convention DisplayQt/AxisPTZStreamQt/CamServerStreamQt already follow
# (see desktop_app.py's module docstring).


class _FakeView:
    def __init__(self, refresh_timer=None):
        if refresh_timer is not None:
            self._refresh_timer = refresh_timer


class _FakeTimer:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_svg_viewer_handle_exposes_window():
    view = _FakeView()
    handle = svg_interactor._SvgViewerHandle(view)
    assert handle.window is view


def test_svg_viewer_handle_stop_stops_the_refresh_timer_if_any():
    timer = _FakeTimer()
    handle = svg_interactor._SvgViewerHandle(_FakeView(refresh_timer=timer))
    handle.stop()
    assert timer.stopped is True


def test_svg_viewer_handle_stop_is_a_no_op_without_a_refresh_timer():
    handle = svg_interactor._SvgViewerHandle(_FakeView())
    handle.stop()  # must not raise


# -- launch_svg_viewer: backend / dock_in --------------------------------


def test_launch_svg_viewer_window_backend_reports_missing_webengine_clearly(monkeypatch, tmp_path):
    """Documents a real environment gap: qtpy's default binding choice can
    have no WebEngine support installed even when a different, also-
    installed binding does (this is exactly what's broken in the deployed
    bpy312 env today -- PyQt5 is picked, PyQtWebEngine isn't installed,
    even though PySide6+its WebEngine both are). Must fail with a clear,
    actionable message, not a raw traceback."""
    shell = _make_shell("TerminalInteractiveShell")
    monkeypatch.setattr(svg_interactor, "get_ipython", lambda: shell)
    monkeypatch.setitem(sys.modules, "qtpy.QtWebEngineWidgets", None)  # force ImportError on import

    svg_path = tmp_path / "x.svg"
    svg_path.write_text("<svg width='10' height='10'></svg>")

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))

    result = svg_interactor.launch_svg_viewer(str(svg_path), backend="window")

    assert result is None
    assert any("WebEngine" in line for line in printed)


def test_launch_svg_viewer_docks_into_desktop_app_instead_of_opening_standalone(monkeypatch, tmp_path):
    shell = _make_shell("TerminalInteractiveShell", active_eventloop=None)
    monkeypatch.setattr(svg_interactor, "get_ipython", lambda: shell)

    fake_qtwidgets = types.ModuleType("qtpy.QtWidgets")
    fake_qtwidgets.QApplication = object
    fake_webengine = types.ModuleType("qtpy.QtWebEngineWidgets")
    fake_webengine.QWebEngineView = object
    monkeypatch.setitem(sys.modules, "qtpy.QtWidgets", fake_qtwidgets)
    monkeypatch.setitem(sys.modules, "qtpy.QtWebEngineWidgets", fake_webengine)

    calls = {}

    def fake_build_qt_window(svg_path, ip_instance, namespace_prefix, exclude_group_ids,
                              blocking=False, refresh=None, refresh_interval_ms=2000,
                              dock_in=None, dock_name=None):
        calls.update(
            svg_path=svg_path, blocking=blocking, dock_in=dock_in, dock_name=dock_name,
        )

    monkeypatch.setattr(svg_interactor, "_build_qt_window", fake_build_qt_window)

    svg_path = tmp_path / "x.svg"
    svg_path.write_text("<svg width='10' height='10'></svg>")

    class _FakeDesktopApp:
        def _dock_widget_object(self, name, widget_obj):
            pass  # not exercised here -- _build_qt_window is faked out above

    app = _FakeDesktopApp()
    svg_interactor.launch_svg_viewer(
        str(svg_path), backend="window", dock_in=app, dock_name="bernina",
    )

    assert calls["dock_in"] is app
    assert calls["dock_name"] == "bernina"
    # docking into an already-running EcoDesktopApp event loop -- no
    # blocking-mode fallback logic should kick in, unlike the standalone
    # top-level-window path.
    assert calls["blocking"] is False


def test_build_qt_window_dock_in_calls_dock_widget_object_not_show(monkeypatch, tmp_path):
    """The real dock_in wiring inside _build_qt_window itself: given a
    dock_in, it must hand the view to _dock_widget_object instead of
    calling view.show()/registering it in the standalone-window list."""
    fake_qtcore = types.ModuleType("qtpy.QtCore")
    fake_qtcore.Qt = types.SimpleNamespace(AA_ShareOpenGLContexts=1, WA_QuitOnClose=2)
    fake_qtcore.QObject = object
    fake_qtcore.QUrl = types.SimpleNamespace(fromLocalFile=lambda p: p)

    def _slot(*a, **kw):
        return lambda f: f

    fake_qtcore.Slot = _slot
    fake_qtcore.QTimer = lambda *a, **kw: types.SimpleNamespace(
        timeout=types.SimpleNamespace(connect=lambda cb: None), start=lambda ms: None,
    )

    class _FakeApp:
        _instance = None

        @classmethod
        def instance(cls):
            return cls._instance

        def __init__(self, *a):
            _FakeApp._instance = self

        @staticmethod
        def setAttribute(*a):
            pass

    fake_qtwidgets = types.ModuleType("qtpy.QtWidgets")
    fake_qtwidgets.QApplication = _FakeApp

    fake_webchannel_mod = types.ModuleType("qtpy.QtWebChannel")

    class _FakeChannel:
        def registerObject(self, *a):
            pass

    fake_webchannel_mod.QWebChannel = _FakeChannel

    docked = []

    class _FakeView:
        def __init__(self):
            self.shown = False

        def page(self):
            return types.SimpleNamespace(setWebChannel=lambda ch: None)

        def setHtml(self, *a):
            pass

        def setWindowTitle(self, *a):
            pass

        def resize(self, *a):
            pass

        def show(self):
            self.shown = True

        def setAttribute(self, *a):
            pass

    fake_webengine_mod = types.ModuleType("qtpy.QtWebEngineWidgets")
    fake_webengine_mod.QWebEngineView = _FakeView

    monkeypatch.setitem(sys.modules, "qtpy.QtCore", fake_qtcore)
    monkeypatch.setitem(sys.modules, "qtpy.QtWidgets", fake_qtwidgets)
    monkeypatch.setitem(sys.modules, "qtpy.QtWebChannel", fake_webchannel_mod)
    monkeypatch.setitem(sys.modules, "qtpy.QtWebEngineWidgets", fake_webengine_mod)
    monkeypatch.setattr(svg_interactor, "_qt_windows", [])

    class _FakeDesktopApp:
        def __init__(self):
            self.docked = []

        def _dock_widget_object(self, name, widget_obj):
            self.docked.append((name, widget_obj))

    svg_path = tmp_path / "x.svg"
    svg_path.write_text("<svg width='10' height='10'></svg>")

    app = _FakeDesktopApp()
    svg_interactor._build_qt_window(
        str(svg_path), ip_instance=object(), dock_in=app, dock_name="bernina",
    )

    assert len(app.docked) == 1
    name, handle = app.docked[0]
    assert name == "bernina"
    assert isinstance(handle, svg_interactor._SvgViewerHandle)
    assert handle.window.shown is False  # dock_in path must not self-show
    assert svg_interactor._qt_windows == []  # and must not register as a standalone window either


# -- launch_svg_viewer: notebook / sidecar_anchor ------------------------


class _FakeSidecar:
    instances = []

    def __init__(self, title=None, anchor=None):
        self.title = title
        self.anchor = anchor
        self.entered = False
        _FakeSidecar.instances.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        return False


class _FakeThread:
    """threading.Thread stand-in that never actually starts the Dash
    server -- these tests only care about the sidecar/display wiring."""

    def __init__(self, target=None, args=(), daemon=None):
        pass

    def start(self):
        pass


@pytest.fixture(autouse=True)
def _reset_fake_sidecar():
    _FakeSidecar.instances = []
    yield
    _FakeSidecar.instances = []


def test_launch_svg_viewer_sidecar_anchor_missing_dependency_message(monkeypatch, tmp_path):
    shell = _make_shell("ZMQInteractiveShell")
    monkeypatch.setattr(svg_interactor, "get_ipython", lambda: shell)
    monkeypatch.setattr(svg_interactor.threading, "Thread", _FakeThread)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sidecar":
            raise ImportError("no module named sidecar")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    svg_path = tmp_path / "x.svg"
    svg_path.write_text("<svg width='10' height='10'></svg>")

    with pytest.raises(RuntimeError, match="pip install sidecar"):
        svg_interactor.launch_svg_viewer(str(svg_path), sidecar_anchor="split-right")


def test_launch_svg_viewer_sidecar_anchor_opens_iframe_in_a_sidecar_panel(monkeypatch, tmp_path):
    shell = _make_shell("ZMQInteractiveShell")
    monkeypatch.setattr(svg_interactor, "get_ipython", lambda: shell)
    monkeypatch.setattr(svg_interactor.threading, "Thread", _FakeThread)

    fake_sidecar_mod = types.ModuleType("sidecar")
    fake_sidecar_mod.Sidecar = _FakeSidecar
    monkeypatch.setitem(sys.modules, "sidecar", fake_sidecar_mod)

    displayed = []
    monkeypatch.setattr("IPython.display.display", lambda obj: displayed.append(obj))

    svg_path = tmp_path / "x.svg"
    svg_path.write_text("<svg width='800' height='400'></svg>")

    result = svg_interactor.launch_svg_viewer(
        str(svg_path), namespace_prefix="bernina", sidecar_anchor="split-right", dock_name="bernina",
    )

    assert isinstance(result, _FakeSidecar)
    assert result.anchor == "split-right"
    assert result.title == "bernina"
    assert result.entered is True
    # the IFrame was displayed *while* the Sidecar context was active, not
    # into the plain current-cell output.
    assert len(displayed) == 1
    from IPython.display import IFrame

    assert isinstance(displayed[0], IFrame)


def test_launch_svg_viewer_without_sidecar_anchor_displays_inline_as_before(monkeypatch, tmp_path):
    """No sidecar_anchor given -- must keep today's plain inline-in-the-
    current-cell behaviour (Voila/classic Notebook compatibility), not
    silently start trying to use Sidecar."""
    shell = _make_shell("ZMQInteractiveShell")
    monkeypatch.setattr(svg_interactor, "get_ipython", lambda: shell)
    monkeypatch.setattr(svg_interactor.threading, "Thread", _FakeThread)

    displayed = []
    monkeypatch.setattr("IPython.display.display", lambda obj: displayed.append(obj))

    svg_path = tmp_path / "x.svg"
    svg_path.write_text("<svg width='800' height='400'></svg>")

    result = svg_interactor.launch_svg_viewer(str(svg_path))

    assert result is None
    assert len(displayed) == 1
    assert _FakeSidecar.instances == []
