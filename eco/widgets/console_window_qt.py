"""A standalone, independent Qt console window -- eco.start_console(kind="qt").

Unlike eco.widgets.desktop_app.EcoDesktopApp (which shares the calling
session's namespace object in-process whenever that's safe -- see
eco.widgets.console_kernel), this always opens a genuinely separate kernel
subprocess: the point of "give me a new console" is a second, independent
working session, not another view onto the one you're already in. It
builds its own copy of the requested namespace as its first executed
cell, and (like every console built through eco.widgets.console_kernel) has
its own eco.widgets.kernel_registry.KernelSession logging everything typed
into it and everything that comes back.

No namespace launcher dock here (unlike EcoDesktopApp) -- just the console
itself, kept deliberately lightweight for "I want another prompt," not
another full workbench.
"""
import logging

from qtpy import QtCore, QtWidgets

logger = logging.getLogger(__name__)

_app_ref = None  # keep a strong reference to any QApplication we create ourselves


class ConsoleWindowQt:
    """Lifecycle wrapper mirroring eco.widgets.component_selector_qt.
    ComponentSelectorQtWindow / eco.widgets.desktop_app.EcoDesktopApp:
    start() (non-blocking inside IPython), run() (blocking), stop()."""

    def __init__(self, scope="bernina", lazy=True, theme=None, label=None, auto_start=True):
        self.scope = scope
        self.lazy = lazy
        self.theme = theme
        self.label = label or scope
        self.window = None
        self._console = None
        self._kernel_manager = None
        self._kernel_client = None
        self._kernel_session = None
        if auto_start:
            self.start()

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            _app_ref = QtWidgets.QApplication([])
        from eco.widgets.console_kernel import build_console_widget, build_subprocess_kernel
        from eco.widgets.qt_theme import apply_modern_theme

        apply_modern_theme(self.theme)

        startup_code = (
            "from eco.widgets.desktop_app import build_namespace\n"
            f"namespace = build_namespace(scope={self.scope!r}, lazy={self.lazy!r})"
        )
        self._kernel_manager, self._kernel_client, self._kernel_session = build_subprocess_kernel(
            kind="console", label=self.label
        )
        banner = (
            f"eco console ({self.label})\n"
            "an independent kernel, running in its own process -- nothing here "
            "is shared with any other console or terminal.\n"
            "'namespace' is being (re)built now; give it a moment on a lazy=False "
            "scope.\n"
        )
        console = build_console_widget(
            self._kernel_manager,
            self._kernel_client,
            self._kernel_session,
            banner=banner,
            startup_code=startup_code,
        )
        self._console = console

        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(f"eco console: {self.label}")
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        self.window.setCentralWidget(console)
        # close_calls_stop, not a plain destroyed.connect(setattr(...)):
        # this window's kernel is a real subprocess, which Qt's own
        # child-deletion never terminates on its own -- closing via the
        # window's native close (X) button needs to actually run stop()
        # (which shuts the kernel down), not just clear a reference. See
        # eco.widgets.qt_lifecycle's module docstring for the fuller why.
        from eco.widgets.qt_lifecycle import close_calls_stop

        close_calls_stop(self.window, self.stop)
        self.window.resize(820, 620)
        self.window.show()

    def run(self):
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self):
        if self.window is not None:
            return
        ip = None
        try:
            from IPython import get_ipython

            ip = get_ipython()
        except Exception:
            ip = None

        if ip is None:
            self.run()
            return

        active = getattr(ip, "active_eventloop", None)
        if active is None:
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco console: a different GUI event loop ('{active}') is already "
                "active in this IPython session, so the window can't be pumped "
                "non-blockingly alongside it. Showing it in blocking mode instead."
            )
            self.run()
            return

        self._build_window()

    def stop(self):
        from eco.widgets.console_kernel import stop_kernel

        stop_kernel(self._kernel_manager, self._kernel_client, self._kernel_session)
        self._kernel_manager = None
        self._kernel_client = None
        self._kernel_session = None
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


def make_console_window_qt(scope="bernina", lazy=True, theme=None, label=None, auto_start=True):
    return ConsoleWindowQt(scope=scope, lazy=lazy, theme=theme, label=label, auto_start=auto_start)
