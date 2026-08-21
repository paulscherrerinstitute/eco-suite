import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

import eco.widgets.desktop_app as desktop_app
from eco.widgets.desktop_app import (
    EcoDesktopApp,
    _dock_object_name,
    _NamespaceLauncher,
    _qbytearray_to_str,
    _str_to_qbytearray,
    sorted_namespace_entries,
)


@pytest.fixture(autouse=True)
def _no_real_home_writes(tmp_path, monkeypatch):
    # autosave-on-close (and save_workspace()/load_workspace() called
    # with no explicit path) default to ~/.eco/desktop_workspace.json --
    # redirect that for every test in this file so nothing here ever
    # touches the real account's home directory
    monkeypatch.setattr(desktop_app, "DEFAULT_WORKSPACE_FILE", tmp_path / "desktop_workspace.json")


class _FakeWidgetWrapper:
    """Mirrors the .window/.stop() convention every real eco Qt widget
    wrapper follows (DisplayQt, AxisPTZStreamQt, CamServerStreamQt, ...) --
    what EcoDesktopApp._dock_widget_object actually looks for."""

    def __init__(self):
        self.window = QtWidgets.QWidget()
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1


class _FakeItem:
    """Stands in for a real namespace component -- just enough for
    EcoDesktopApp._open_widget's getattr(namespace, name).widget() to
    have something real to call."""

    def __init__(self, name):
        self.name = name
        self.widget_calls = 0
        self.last_widget = None

    def widget(self):
        self.widget_calls += 1
        self.last_widget = _FakeWidgetWrapper()
        return self.last_widget


class _FakeNamespace:
    def __init__(self, initialized=(), lazy=(), failed=(), init_delay=0.0):
        self.initialized_names = set(initialized)
        self.lazy_names = set(lazy)
        self.failed_names = set(failed)
        self._init_delay = init_delay
        self.init_calls = []
        self.init_all_calls = []
        self._required = set()
        # a real Namespace resolves a name via resolve_item (lazy_items-or-
        # failed_items-or-initialized_items -- see Namespace.resolve_item's
        # docstring for why NOT a bare attribute); mirror that with one
        # flat dict here rather than three, since this fake never needs to
        # tell "which dict" apart from the *_names sets above.
        self._items = {}
        for name in set(initialized) | set(lazy) | set(failed):
            self._items[name] = _FakeItem(name)

    def resolve_item(self, name):
        return self._items.get(name)

    def required_names(self, value=None):
        if value is None:
            return sorted(self._required)
        self._required = set(value)

    def init_name(self, name, raise_errors=False, **kwargs):
        self.init_calls.append(name)
        if self._init_delay:
            time.sleep(self._init_delay)
        if name in self.lazy_names:
            self.lazy_names.discard(name)
            self.initialized_names.add(name)

    def init_all(self, required_only=True, background=True, raise_errors=False, **kwargs):
        self.init_all_calls.append(
            {"required_only": required_only, "background": background}
        )
        for name in list(self.lazy_names):
            self.init_name(name, raise_errors=raise_errors)


def test_sorted_namespace_entries_labels_and_orders():
    ns = _FakeNamespace(initialized=["beta", "alpha"], lazy=["zulu"], failed=["gamma"])
    entries = sorted_namespace_entries(ns)
    assert entries == [
        ("alpha", "initialized"),
        ("beta", "initialized"),
        ("gamma", "failed"),
        ("zulu", "lazy"),
    ]


def test_sorted_namespace_entries_empty_namespace():
    ns = _FakeNamespace()
    assert sorted_namespace_entries(ns) == []


def _pump(app, predicate, timeout=10.0, interval=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_namespace_launcher_opens_initialized_entry_immediately():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    opened = []
    launcher = _NamespaceLauncher(ns, on_open=opened.append)

    assert launcher._list.rowCount() == 1
    item = launcher._list.item(0, launcher._COL_NAME)
    assert item.text().endswith(" cam_west")  # icon prefix -- see _kind_icon
    launcher._on_item_clicked(item)

    assert opened == ["cam_west"]
    assert ns.init_calls == []  # already initialized -- no init needed


def test_namespace_launcher_initializes_lazy_entry_then_opens_it():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["spectrometer"], init_delay=0.05)
    opened = []
    launcher = _NamespaceLauncher(ns, on_open=opened.append)

    item = launcher._list.item(0, launcher._COL_NAME)
    launcher._on_item_clicked(item)

    # while loading, the entry is tracked (spinner shown via _label_for)
    assert "spectrometer" in launcher._loading

    resolved = _pump(app, lambda: opened == ["spectrometer"], timeout=5.0)
    assert resolved, "lazy entry should have initialized and then opened"
    assert ns.init_calls == ["spectrometer"]
    assert "spectrometer" not in launcher._loading


def test_namespace_launcher_lazy_init_thread_attaches_the_shared_ca_context(monkeypatch):
    """Regression test for a real crash: a background thread that touches
    Channel Access without first attaching to the process's one shared
    "initial context" (epics.ca.use_initial_context()) can implicitly
    create its own separate CA context instead -- a documented segfault/
    corruption source (see eco.utilities.config.Namespace._init_batch's
    and eco.status_server.parallel_init's module docstrings) that this
    session hit for real: eco.start_desktop() from a running terminal,
    clicking a lazy namespace entry to initialize it, aborted the whole
    process with a pthread mutex assertion failure."""
    import epics.ca as ca

    calls = []
    monkeypatch.setattr(ca, "use_initial_context", lambda: calls.append(True))

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["prepump"], init_delay=0.05)
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)

    launcher._on_item_clicked(launcher._list.item(0, launcher._COL_NAME))
    resolved = _pump(app, lambda: ns.init_calls == ["prepump"], timeout=5.0)

    assert resolved
    assert calls, "the background init thread must call use_initial_context() before touching CA"


def test_namespace_launcher_does_not_open_failed_entry():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(failed=["broken_device"])
    opened = []
    launcher = _NamespaceLauncher(ns, on_open=opened.append)

    item = launcher._list.item(0, launcher._COL_NAME)
    assert item.text().endswith(" broken_device")
    assert "⚠" in item.text()  # failed-state icon, see _kind_icon
    launcher._on_item_clicked(item)

    assert opened == []
    assert ns.init_calls == []


def test_namespace_launcher_filter_hides_non_matching_entries():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west", "cam_east", "slit_1"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)

    launcher._filter_edit.setText("cam")
    names = {
        launcher._list.item(i, launcher._COL_NAME).data(QtCore.Qt.UserRole)
        for i in range(launcher._list.rowCount())
    }
    assert names == {"cam_west", "cam_east"}


def test_namespace_launcher_clicking_already_loading_entry_is_a_no_op():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["slow_device"], init_delay=1.0)
    opened = []
    launcher = _NamespaceLauncher(ns, on_open=opened.append)

    item = launcher._list.item(0, launcher._COL_NAME)
    launcher._on_item_clicked(item)  # starts loading
    assert "slow_device" in launcher._loading

    launcher._refresh()
    item2 = launcher._list.item(0, launcher._COL_NAME)
    launcher._on_item_clicked(item2)  # should not start a second init

    time.sleep(0.1)
    assert ns.init_calls == ["slow_device"]


# -- kind icons --


class _FakeAdjustableItem:
    def get_current_value(self):
        return 0

    def set_target_value(self, value):
        pass


class _FakeDetectorItem:
    def get_current_value(self):
        return 0


def test_kind_icon_for_lazy_and_failed_states_never_resolves_the_item():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["l"], failed=["f"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    assert launcher._kind_icon("l", "lazy") == "⏳"
    assert launcher._kind_icon("f", "failed") == "⚠️"


def test_kind_icon_classifies_initialized_adjustable_and_detector():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["motor1", "diode1"])
    ns._items["motor1"] = _FakeAdjustableItem()
    ns._items["diode1"] = _FakeDetectorItem()
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    assert launcher._kind_icon("motor1", "initialized") == "✏️"
    assert launcher._kind_icon("diode1", "initialized") == "\U0001f441️"


def test_kind_icon_falls_back_to_other_for_a_plain_object():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["thing"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    assert launcher._kind_icon("thing", "initialized") == "•"  # _FakeItem has no get_current_value


# -- Init All --


def test_init_all_button_disabled_with_nothing_lazy():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    assert launcher._init_all_btn.isEnabled() is False


def test_init_all_button_enabled_when_something_is_lazy():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["spectrometer"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    assert launcher._init_all_btn.isEnabled() is True


def test_init_all_click_initializes_everything_lazy_without_opening_any():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["a", "b", "c"], init_delay=0.02)
    opened = []
    launcher = _NamespaceLauncher(ns, on_open=opened.append)

    launcher._on_init_all_clicked()
    assert {"a", "b", "c"} <= launcher._loading

    resolved = _pump(app, lambda: not launcher._loading, timeout=5.0)
    assert resolved
    assert set(ns.init_calls) == {"a", "b", "c"}
    assert ns.lazy_names == set()
    assert ns.initialized_names == {"a", "b", "c"}
    assert opened == []  # Init All never auto-opens, unlike a single click
    assert ns.init_all_calls == [{"required_only": False, "background": False}]


def test_init_all_click_with_nothing_lazy_is_a_no_op():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    launcher._on_init_all_clicked()
    assert ns.init_all_calls == []
    assert launcher._loading == set()


# -- live refresh timer --


def test_live_refresh_timer_is_running_and_wired_to_refresh():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    assert launcher._live_refresh_timer.isActive()
    assert launcher._live_refresh_timer.interval() == _NamespaceLauncher.LIVE_REFRESH_INTERVAL_MS


def test_live_refresh_picks_up_a_change_made_outside_the_launcher():
    """The point of the timer: something else (a console, another init_all
    call, ...) changes namespace state without going through this
    launcher at all -- the next tick should still reflect it."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(lazy=["spectrometer"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    # lazy, not actively loading -- plain name, no icon (grayed out via
    # colour alone; see _label_for -- the hourglass is reserved for
    # self._loading, i.e. actually initializing right now)
    assert launcher._list.item(0, launcher._COL_NAME).text() == "spectrometer"

    # simulate an external initialization (not via this launcher)
    ns.lazy_names.discard("spectrometer")
    ns.initialized_names.add("spectrometer")

    launcher._refresh()  # what the timer's own tick would trigger
    # now initialized -- gets a real kind icon prefix
    text = launcher._list.item(0, launcher._COL_NAME).text()
    assert text.endswith(" spectrometer")
    assert text != "spectrometer"


# -- Required column (checkable items, not QCheckBox cell widgets) --
#
# Regression coverage for a real reported bug: the live-refresh timer (every
# 2s) rebuilds every row, and a QCheckBox+QWidget+QHBoxLayout cell widget per
# row (the original implementation) meant constructing/destroying ~120 real
# Qt widgets twice a second at bernina's scale -- visible GUI-thread lag,
# reported after `eco desktop` testing. Checkable QTableWidgetItems carry the
# same state far more cheaply (see _refresh's comment).


def test_required_column_is_a_checkable_item_not_a_cell_widget():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    ns.required_names(["cam_west"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)

    assert launcher._list.cellWidget(0, launcher._COL_REQUIRED) is None
    item = launcher._list.item(0, launcher._COL_REQUIRED)
    assert item.flags() & QtCore.Qt.ItemIsUserCheckable
    assert item.checkState() == QtCore.Qt.Checked


def test_toggling_required_checkbox_updates_namespace_required_names():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)
    item = launcher._list.item(0, launcher._COL_REQUIRED)
    assert item.checkState() == QtCore.Qt.Unchecked

    item.setCheckState(QtCore.Qt.Checked)
    assert ns.required_names() == ["cam_west"]

    item.setCheckState(QtCore.Qt.Unchecked)
    assert ns.required_names() == []


def test_clicking_required_checkbox_does_not_open_or_init():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    opened = []
    launcher = _NamespaceLauncher(ns, on_open=opened.append)

    launcher._on_item_clicked(launcher._list.item(0, launcher._COL_REQUIRED))

    assert opened == []


def test_refresh_does_not_spuriously_rewrite_required_names():
    """The point of blockSignals() around the rebuild loop: setCheckState()
    during a routine (every-2s) refresh must not itself look like a user
    edit and trigger a required_names() write -- that would mean writing
    to Namespace.required_names()'s backing file on every tick, whether or
    not anything actually changed."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    ns.required_names(["cam_west"])
    launcher = _NamespaceLauncher(ns, on_open=lambda name: None)

    write_calls = []
    real_required_names = ns.required_names

    def spying_required_names(value=None):
        if value is not None:
            write_calls.append(value)
        return real_required_names(value)

    ns.required_names = spying_required_names
    launcher._refresh()
    launcher._refresh()

    assert write_calls == []


# -- link_terminal --


def test_terminal_user_ns_returns_the_dict_when_ipython_session_exists(monkeypatch):
    terminal_ns = {"foo": 1}

    class FakeIPython:
        user_ns = terminal_ns

    monkeypatch.setattr("IPython.get_ipython", lambda: FakeIPython())

    app = EcoDesktopApp.__new__(EcoDesktopApp)  # skip __init__/auto_start
    assert app._terminal_user_ns() is terminal_ns  # literally the same dict, not a copy


def test_terminal_user_ns_returns_none_with_no_ipython_session(monkeypatch):
    monkeypatch.setattr("IPython.get_ipython", lambda: None)

    app = EcoDesktopApp.__new__(EcoDesktopApp)
    assert app._terminal_user_ns() is None


# -- _build_console kernel-flavour branching --
#
# Regression coverage for the real bug this replaced: calling
# eco.start_desktop() from a running `ipython` terminal session used to
# unconditionally try to build an in-process kernel, which raises
# ipykernel's MultipleInstanceError the moment a TerminalInteractiveShell
# (or any other InteractiveShell subclass) already owns this process's
# IPython singleton -- see eco.widgets.console_kernel's module docstring.
# _build_console must now check can_use_inprocess_kernel() first and fall
# back to a real subprocess kernel whenever that's False.


class _FakeSession:
    def log_event(self, *a, **kw):
        pass


class _FakeConsoleWidget:
    """Standing in for console_kernel.LoggingJupyterWidget in these
    dispatch-logic tests: constructing a *real* RichJupyterWidget subclass
    here has been observed to crash the interpreter outright under
    pytest+offscreen+eco's full heavy scientific-stack imports (the same
    native-library-interaction fragility noted for QDial construction in
    tests/test_indicator_widgets.py) -- these tests only need to verify
    which kernel builder ran and what banner text resulted, not that a
    real console widget renders."""

    def __init__(self, session=None):
        self.session = session
        self.kernel_manager = None
        self.kernel_client = None
        self.banner = ""
        self.executed = []

    def execute(self, code, hidden=False):
        self.executed.append(code)


def _make_app(namespace="the-namespace", link_terminal=True, scope="bernina", lazy=True):
    app = EcoDesktopApp.__new__(EcoDesktopApp)
    app.namespace = namespace
    app.link_terminal = link_terminal
    app.scope = scope
    app.lazy = lazy
    return app


def test_build_console_uses_inprocess_kernel_when_safe(monkeypatch):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls = {}

    def fake_build_inprocess(kind, label=None, shared_user_ns=None, push_vars=None):
        calls["shared_user_ns"] = shared_user_ns
        calls["push_vars"] = push_vars
        return "manager", "client", _FakeSession()

    def fake_build_subprocess(*a, **kw):
        raise AssertionError("should not use a subprocess kernel when in-process is safe")

    monkeypatch.setattr("eco.widgets.console_kernel.can_use_inprocess_kernel", lambda: True)
    monkeypatch.setattr("eco.widgets.console_kernel.build_inprocess_kernel", fake_build_inprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.build_subprocess_kernel", fake_build_subprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.LoggingJupyterWidget", _FakeConsoleWidget)
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    # build_namespace_vars does a real `importlib.import_module(f"eco.{scope}")`
    # -- fine in production (EcoDesktopApp.namespace was already built from
    # that same import, so it's cached; see build_namespace_vars's
    # docstring), but this test's `app.namespace` is just the bare string
    # "the-namespace", never built via a real import at all -- fake it out
    # so this test stays instant instead of doing a real, first-time
    # eco.bernina import.
    monkeypatch.setattr(
        "eco.widgets.desktop_app.build_namespace_vars",
        lambda scope, lazy: {"mono": "fake-mono-value"},
    )

    app = _make_app()
    app._build_console()

    assert app._kernel_manager == "manager"
    assert calls["push_vars"] == {"mono": "fake-mono-value", "namespace": "the-namespace"}
    assert calls["shared_user_ns"] is None
    assert "linked to the calling terminal" not in app._console.banner
    # in-process: namespace is pushed directly, no startup code to run --
    # only the jedi-disable call every console gets (see
    # console_kernel.build_console_widget's docstring)
    assert app._console.executed == ["get_ipython().Completer.use_jedi = False"]


def test_build_console_shares_terminal_namespace_when_available(monkeypatch):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    terminal_ns = {}

    class FakeIPython:
        user_ns = terminal_ns

    calls = {}

    def fake_build_inprocess(kind, label=None, shared_user_ns=None, push_vars=None):
        calls["shared_user_ns"] = shared_user_ns
        return "manager", "client", _FakeSession()

    monkeypatch.setattr("eco.widgets.console_kernel.can_use_inprocess_kernel", lambda: True)
    monkeypatch.setattr("eco.widgets.console_kernel.build_inprocess_kernel", fake_build_inprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.LoggingJupyterWidget", _FakeConsoleWidget)
    monkeypatch.setattr("IPython.get_ipython", lambda: FakeIPython())

    app = _make_app()
    app._build_console()

    assert calls["shared_user_ns"] is terminal_ns
    assert terminal_ns["namespace"] == "the-namespace"
    assert "linked to the calling terminal" in app._console.banner


def test_build_console_falls_back_to_subprocess_kernel_when_shell_conflict(monkeypatch):
    """The actual MultipleInstanceError scenario: an incompatible IPython
    shell (e.g. a running terminal session) already exists -- must not
    attempt an in-process kernel at all."""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def fake_build_subprocess(kind, label=None):
        return "manager", "client", _FakeSession()

    def fake_build_inprocess(*a, **kw):
        raise AssertionError("must not build an in-process kernel when a conflicting shell exists")

    monkeypatch.setattr("eco.widgets.console_kernel.can_use_inprocess_kernel", lambda: False)
    monkeypatch.setattr("eco.widgets.console_kernel.build_subprocess_kernel", fake_build_subprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.build_inprocess_kernel", fake_build_inprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.LoggingJupyterWidget", _FakeConsoleWidget)

    app = _make_app(scope="bernina", lazy=True)
    app._build_console()

    assert app._kernel_manager == "manager"
    # startup code is run through the finished console widget's own
    # .execute() (not passed to build_subprocess_kernel) -- see
    # build_subprocess_kernel's docstring for why. Mirrors startup_inline.py's
    # own two lines (import eco.<scope> as <scope>; from eco.<scope> import
    # *) so bare names (mono, att, ...) are available here too, not just a
    # `namespace` variable -- see build_namespace_vars's docstring.
    # index 0 is every console's hidden jedi-disable call (see
    # console_kernel.build_console_widget's docstring); index 1 is this
    # subprocess kernel's own scope-loading startup code.
    assert len(app._console.executed) == 2
    assert app._console.executed[1] == (
        "from eco import ecocnf\n"
        "ecocnf.startup_lazy = True\n"
        "import eco.bernina as bernina\n"
        "from eco.bernina import *\n"
    )
    assert "already has a running IPython shell" in app._console.banner


def test_build_console_subprocess_banner_when_link_terminal_false(monkeypatch):
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def fake_build_subprocess(kind, label=None):
        return "manager", "client", _FakeSession()

    monkeypatch.setattr("eco.widgets.console_kernel.can_use_inprocess_kernel", lambda: False)
    monkeypatch.setattr("eco.widgets.console_kernel.build_subprocess_kernel", fake_build_subprocess)
    monkeypatch.setattr("eco.widgets.console_kernel.LoggingJupyterWidget", _FakeConsoleWidget)

    app = _make_app(link_terminal=False)
    app._build_console()

    assert "an independent kernel, running in its own process" in app._console.banner
    # index 0: every console's hidden jedi-disable call; index 1: this
    # subprocess kernel's own scope-loading startup code
    assert len(app._console.executed) == 2


def test_open_widget_calls_widget_directly_not_through_the_console():
    """Regression test for two real bugs the console-routing approach had
    (see the module docstring, "WHY OPENED WIDGETS ARE CALLED DIRECTLY,
    NOT THROUGH THE CONSOLE"): a bare "<name>.widget()" sent to the
    console only resolves if the console's namespace also did the
    equivalent of `from eco.<scope> import *` (true for a console sharing
    the terminal's own user_ns, not true for an independent subprocess
    kernel); and routing through any console at all is fragile (a
    startup-code race could leave `namespace` undefined there). Calling
    the item's .widget() directly sidesteps both -- is_notebook() (which
    Assembly.widget() branches on) reports the same "not a notebook"
    result from the launcher's own thread regardless of what kind of
    kernel backs the console, since get_ipython() there reflects this
    process's own IPython state, untouched by either kernel flavour."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        assert ns._items["prepump"].widget_calls == 1
        assert gui._opened_names == ["prepump"]
    finally:
        gui.stop()


def test_open_widget_logs_and_survives_a_failing_widget():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["broken"])

    def boom():
        raise RuntimeError("no display")

    ns._items["broken"].widget = boom
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("broken")  # must not raise
        assert gui._opened_names == ["broken"]
    finally:
        gui.stop()


# -- opened widgets dock as tiles instead of staying separate windows --


def test_open_widget_docks_the_returned_window_instead_of_leaving_it_standalone():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        wrapper = ns._items["prepump"].last_widget
        assert len(gui._widget_docks) == 1
        dock = gui._widget_docks[0]
        assert dock.widget() is wrapper.window
        # reparented into the dock -- no longer its own top-level window
        assert wrapper.window.isWindow() is False
        assert dock.windowTitle() == "prepump"
    finally:
        gui.stop()


def test_open_widget_dock_is_movable_closable_and_floatable():
    """"can still be detached using the ui button there later" --
    DockWidgetFloatable is what makes that possible; confirm it's set."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        dock = gui._widget_docks[0]
        features = dock.features()
        assert features & QtWidgets.QDockWidget.DockWidgetFloatable
        assert features & QtWidgets.QDockWidget.DockWidgetMovable
        assert features & QtWidgets.QDockWidget.DockWidgetClosable
    finally:
        gui.stop()


def test_open_widget_dock_close_stops_the_wrapped_widget():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        wrapper = ns._items["prepump"].last_widget
        dock = gui._widget_docks[0]
        dock.close()
        assert wrapper.stop_calls == 1
        assert dock not in gui._widget_docks
    finally:
        gui.stop()


def test_open_widget_multiple_tiles_are_separate_docks_not_tabbed():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump", "env"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        gui._open_widget("env")
        assert len(gui._widget_docks) == 2
        dock1, dock2 = gui._widget_docks
        assert dock1.objectName() != dock2.objectName()
        # tiled (split), not stacked into the same tab group
        assert dock2 not in gui.window.tabifiedDockWidgets(dock1)
    finally:
        gui.stop()


def test_open_widget_reopening_same_name_adds_another_tile():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        gui._open_widget("prepump")
        assert len(gui._widget_docks) == 2
        assert gui._widget_docks[0].objectName() != gui._widget_docks[1].objectName()
    finally:
        gui.stop()


def test_dock_widget_object_ignores_a_returned_object_without_window_attribute():
    """Graceful no-op (not a crash) for anything that doesn't follow the
    .window convention -- e.g. a widget() override that returns None or a
    bare non-Qt object."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["odd"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._dock_widget_object("odd", object())  # no .window at all
        gui._dock_widget_object("odd", None)
        assert gui._widget_docks == []
    finally:
        gui.stop()


def test_dock_object_name_dedup():
    assert _dock_object_name("widget_prepump", []) == "widget_prepump"
    assert _dock_object_name("widget_prepump", ["widget_prepump"]) == "widget_prepump_2"
    assert (
        _dock_object_name("widget_prepump", ["widget_prepump", "widget_prepump_2"])
        == "widget_prepump_3"
    )


# -- with_console=False --


def test_with_console_false_skips_console_and_kernel():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, with_console=False, auto_start=False)
    gui._build_window()
    try:
        assert gui._console is None
        assert gui._kernel_manager is None
        assert gui._kernel_client is None
        assert gui._kernel_session is None
        assert isinstance(gui.window.centralWidget(), QtWidgets.QLabel)
        assert gui._launcher is not None  # the Namespace dock still works
    finally:
        gui.stop()


def test_with_console_false_open_widget_still_works():
    """The whole point: opening a widget from the Namespace panel never
    needed the console (see _open_widget) -- confirm that still holds
    with with_console=False, not just that nothing crashes."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, with_console=False, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("prepump")
        assert ns._items["prepump"].widget_calls == 1
    finally:
        gui.stop()


def test_with_console_false_stop_is_safe():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, with_console=False, auto_start=False)
    gui._build_window()
    gui.stop()  # must not raise even though there was never a kernel to stop
    assert gui.window is None


def test_native_close_tears_down_docked_widgets_same_as_stop(tmp_path):
    """Regression test for a real bug: _ManagedDockWidget.closeEvent only
    runs when a dock is closed individually -- closing the whole desktop
    window (its own native X button) used to leave every docked device
    widget's poll thread running, since nothing called dock.close() for
    the window-level close path."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, with_console=False, auto_start=False)
    gui._build_window()
    gui._open_widget("prepump")
    wrapper = ns._items["prepump"].last_widget
    assert len(gui._widget_docks) == 1

    gui.window.close()  # simulates the native X button, not gui.stop()

    # _on_window_closing() (which stops the docked widget) runs
    # synchronously inside closeEvent, so this is already true; only the
    # WA_DeleteOnClose-driven destroyed signal (which nulls gui.window)
    # needs a real event loop turn to actually fire -- see
    # eco.widgets.qt_lifecycle's module docstring
    assert wrapper.stop_calls == 1
    assert gui._widget_docks == []
    QtCore.QTimer.singleShot(200, app.quit)
    app.exec_()
    assert gui.window is None


def test_native_close_still_autosaves_the_workspace(tmp_path, monkeypatch):
    """Ordering matters: autosave reads geometry/state off self.window,
    so it has to run before that gets closed/nulled, not after."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(desktop_app, "DEFAULT_WORKSPACE_FILE", tmp_path / "ws.json")
    ns = _FakeNamespace(initialized=["prepump"])
    gui = EcoDesktopApp(namespace=ns, with_console=False, auto_start=False)
    gui._build_window()
    gui._open_widget("prepump")

    gui.window.close()

    assert (tmp_path / "ws.json").exists()


def test_native_close_quits_the_app_when_run_owns_the_event_loop(monkeypatch):
    """Regression test for a real bug found via manual testing: `eco
    desktop` (run() building its own blocking QApplication and calling
    exec_()) hung forever -- terminal included -- after the window was
    closed via its native X button. WA_QuitOnClose=False on the window
    (set in _build_window, needed so closing it from *inside* an existing
    IPython session via start()'s non-blocking path doesn't kill that
    session) also disables Qt's usual "last window closed -> auto quit"
    behaviour, so nothing ever called QApplication.quit() to end exec_().
    run() now sets self._owns_event_loop = True right before exec_(), and
    _on_window_closing() must call quit() itself when that's set."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()
    gui._owns_event_loop = True  # what run() sets right before exec_()

    real_quit = app.quit
    quit_calls = []

    def spying_quit():
        quit_calls.append(True)
        real_quit()

    monkeypatch.setattr(app, "quit", spying_quit)
    # Safety net so a regression here fails the test instead of hanging
    # the whole run: calls the *real* quit directly, bypassing the spy.
    QtCore.QTimer.singleShot(5000, real_quit)
    QtCore.QTimer.singleShot(50, gui.window.close)

    app.exec_()

    assert quit_calls == [True], "closing the window must call QApplication.quit()"


def test_native_close_does_not_quit_the_app_when_embedded(monkeypatch):
    """The other half of the same fix: when this window was opened
    non-blockingly inside an existing IPython/Qt session (start()'s path,
    self._owns_event_loop left False), closing it must NOT quit the whole
    application -- that would kill the calling session's terminal too."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()
    assert gui._owns_event_loop is False

    quit_calls = []
    monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))

    gui.window.close()

    assert quit_calls == []


def test_run_checks_app_existence_before_build_window_can_create_one(monkeypatch):
    """Regression test for a real bug found via manual testing: `eco
    desktop` opened its window and exited immediately, before the CLI's
    --workspace support restructured run() into `app._build_window();
    app.load_workspace(...); app.run()`. run()'s own `created_app = app
    is None` check must run BEFORE anything else creates the
    QApplication as a side effect -- _build_window() does exactly that,
    so calling it separately first meant that by the time run() ran its
    check, created_app was already False, and the app.exec_() call (and
    everything gated on it) was skipped entirely: run() just returned.
    See run()'s docstring for why --workspace now goes through run()
    itself (`run(workspace=...)`) instead.

    Can't assert on created_app's actual *value* here -- this whole test
    file shares one QApplication across every test (a real one always
    exists by the time this runs), so it's always False regardless of
    this fix. What the fix actually guarantees, and what's checked here,
    is the *order*: the instance() check has to be evaluated before
    _build_window() runs, not after."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)

    call_order = []
    real_instance = QtWidgets.QApplication.instance

    def spy_instance():
        call_order.append("instance_check")
        return real_instance()

    def spy_build_window():
        call_order.append("build_window")
        gui.window = object()  # anything non-None -- skip the real Qt build

    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(spy_instance))
    monkeypatch.setattr(QtWidgets.QApplication, "exec_", lambda self: None)
    gui._build_window = spy_build_window

    gui.run(workspace=None)

    assert call_order.index("instance_check") < call_order.index("build_window")


def test_run_loads_workspace_before_entering_the_event_loop(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui.save_workspace(tmp_path / "ws.json")  # empty, just to have a real file
    import json

    (tmp_path / "ws.json").write_text(json.dumps({"opened_names": ["cam_west"]}))

    monkeypatch.setattr(QtWidgets.QApplication, "exec_", lambda self: None)
    gui.run(workspace=tmp_path / "ws.json")

    assert gui._opened_names == ["cam_west"]


# -- workspace persistence --


def test_qbytearray_str_round_trip():
    original = QtCore.QByteArray(b"\x00\x01\xffsome binary geometry blob")
    restored = _str_to_qbytearray(_qbytearray_to_str(original))
    assert bytes(restored) == bytes(original)


def test_save_workspace_writes_geometry_state_and_opened_names(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west", "cam_east"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("cam_west")
        assert gui._opened_names == ["cam_west"]

        path = tmp_path / "ws.json"
        assert gui.save_workspace(path) is True
        assert path.exists()

        import json

        data = json.loads(path.read_text())
        assert data["opened_names"] == ["cam_west"]
        assert "geometry" in data and "state" in data
    finally:
        gui.stop()


def test_save_workspace_with_no_window_is_a_no_op():
    gui = EcoDesktopApp.__new__(EcoDesktopApp)
    gui.window = None
    assert gui.save_workspace() is False


def test_load_workspace_reopens_previously_open_entries(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("cam_west")
        path = tmp_path / "ws.json"
        gui.save_workspace(path)

        # a *fresh* app/window, as if just started -- nothing open yet
        gui2 = EcoDesktopApp(namespace=ns, auto_start=False)
        gui2._build_window()
        try:
            assert gui2._opened_names == []
            loaded = gui2.load_workspace(path)
            assert loaded is True
            assert gui2._opened_names == ["cam_west"]  # reopened via the launcher
        finally:
            gui2.stop()
    finally:
        gui.stop()


def test_load_workspace_missing_file_returns_false(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp.__new__(EcoDesktopApp)
    gui.window = None
    gui._launcher = None
    assert gui.load_workspace(tmp_path / "does_not_exist.json") is False


def test_autosave_on_window_close_writes_workspace_file(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    gui._open_widget("cam_west")

    # the autouse _no_real_home_writes fixture already redirects
    # DEFAULT_WORKSPACE_FILE to a tmp_path; point it at this test's own
    # file specifically so we can assert on it directly
    path = tmp_path / "autosave.json"
    desktop_app.DEFAULT_WORKSPACE_FILE = path
    gui.window.close()  # triggers _DesktopMainWindow.closeEvent -> autosave

    assert path.exists()


def test_new_workspace_action_clears_opened_names():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    gui = EcoDesktopApp(namespace=ns, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("cam_west")
        assert gui._opened_names == ["cam_west"]
        gui._on_new_workspace()
        assert gui._opened_names == []
    finally:
        gui.stop()


# -- Tools menu: Log Viewer -----------------------------------------------


def test_tools_menu_present():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()
    try:
        assert any(a.text() == "&Tools" for a in gui.window.menuBar().actions())
    finally:
        gui.stop()


def test_open_log_viewer_opens_and_stores_a_reference(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()

    class _FakeLogViewer:
        stop_calls = 0

        def stop(self):
            self.stop_calls += 1

    fake = _FakeLogViewer()
    monkeypatch.setattr("eco.logs.widget", lambda prefer: fake)

    try:
        gui._open_log_viewer()
        assert gui._log_viewer is fake
    finally:
        gui.stop()


def test_open_log_viewer_again_replaces_not_piles_up(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()

    class _FakeLogViewer:
        def __init__(self):
            self.stop_calls = 0

        def stop(self):
            self.stop_calls += 1

    made = []

    def fake_widget(prefer):
        v = _FakeLogViewer()
        made.append(v)
        return v

    monkeypatch.setattr("eco.logs.widget", fake_widget)

    try:
        gui._open_log_viewer()
        first = gui._log_viewer
        gui._open_log_viewer()
        assert gui._log_viewer is made[1]
        assert gui._log_viewer is not first
        assert first.stop_calls == 1  # the previous one was torn down, not left running
    finally:
        gui.stop()


def test_log_viewer_stopped_on_window_close(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()

    class _FakeLogViewer:
        stop_calls = 0

        def stop(self):
            self.stop_calls += 1

    fake = _FakeLogViewer()
    monkeypatch.setattr("eco.logs.widget", lambda prefer: fake)
    gui._open_log_viewer()

    gui.stop()

    assert fake.stop_calls == 1
    assert gui._log_viewer is None


# -- Save Startup Script -------------------------------------------------
#
# New feature: a standalone .sh (paired with a .json workspace file) that
# relaunches `eco desktop` with just this session's open widgets reopened,
# for a fast, minimal "dashboard" -- lazy loading means nothing else in the
# namespace gets touched.


def test_save_startup_script_writes_executable_sh_and_companion_json(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ns = _FakeNamespace(initialized=["cam_west"])
    gui = EcoDesktopApp(namespace=ns, scope="bernina", lazy=True, auto_start=False)
    gui._build_window()
    try:
        gui._open_widget("cam_west")
        sh_path = gui.save_startup_script(tmp_path / "my_dashboard.sh")

        assert sh_path == tmp_path / "my_dashboard.sh"
        assert sh_path.exists()
        assert sh_path.stat().st_mode & 0o111  # executable

        json_path = tmp_path / "my_dashboard.json"
        assert json_path.exists()
        import json as _json

        data = _json.loads(json_path.read_text())
        assert data["opened_names"] == ["cam_west"]

        script = sh_path.read_text()
        assert "eco desktop -s bernina -l --workspace" in script
        assert str(json_path) in script
    finally:
        gui.stop()


def test_save_startup_script_adds_sh_suffix_if_missing(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, auto_start=False)
    gui._build_window()
    try:
        sh_path = gui.save_startup_script(tmp_path / "no_extension")
        assert sh_path == tmp_path / "no_extension.sh"
        assert sh_path.exists()
    finally:
        gui.stop()


def test_save_startup_script_omits_scope_flag_when_no_scope(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, scope=None, auto_start=False)
    gui._build_window()
    try:
        sh_path = gui.save_startup_script(tmp_path / "console_only.sh")
        script = sh_path.read_text()
        assert "-s " not in script
        assert "eco desktop -l --workspace" in script
    finally:
        gui.stop()


def test_save_startup_script_respects_no_lazy(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = EcoDesktopApp(namespace=None, scope="bernina", lazy=False, auto_start=False)
    gui._build_window()
    try:
        sh_path = gui.save_startup_script(tmp_path / "eager_dashboard.sh")
        script = sh_path.read_text()
        assert "--no-lazy" in script
    finally:
        gui.stop()


def test_save_startup_script_returns_none_with_no_window():
    gui = EcoDesktopApp.__new__(EcoDesktopApp)
    gui.window = None
    assert gui.save_startup_script("/tmp/whatever.sh") is None


# -- dark theme default ---------------------------------------------------


def test_desktop_app_defaults_to_dark_theme():
    import inspect

    assert inspect.signature(EcoDesktopApp.__init__).parameters["theme"].default == "dark"


def test_main_theme_defaults_to_dark(monkeypatch):
    """--theme's argparse default, and that 'none' maps back to Python
    None (native style) -- see _main()'s `theme = None if args.theme ==
    'none' else args.theme` translation."""

    class _StubApp:
        def run(self, workspace=None):
            pass

    captured = {}

    def fake_desktop_app(namespace, theme, **kwargs):
        captured["theme"] = theme
        return _StubApp()

    monkeypatch.setattr(desktop_app, "EcoDesktopApp", fake_desktop_app)
    monkeypatch.setattr(desktop_app, "build_namespace", lambda **kw: None)
    desktop_app._main([])
    assert captured["theme"] == "dark"

    desktop_app._main(["--theme", "none"])
    assert captured["theme"] is None

    desktop_app._main(["--theme", "light"])
    assert captured["theme"] == "light"
