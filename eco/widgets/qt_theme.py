"""
Optional modern theme for eco's Qt widgets, applied globally via a single
switch. Off by default (native OS style), so nothing changes unless
explicitly opted in.

"dark"/"light" use the real `qt-material` package (Material-Design-style
Qt stylesheets -- teal-on-charcoal by default) when it's installed
(``pip install qt-material`` in the env eco runs in); otherwise this falls
back to a from-scratch QSS approximating the same look, so the theme
switch still works with no new dependency, just closer to qt-material's
exact look once that package is actually present.

Usage::

    from eco.widgets.qt_theme import apply_modern_theme
    apply_modern_theme("dark")   # or "light", or None to clear
    apply_modern_theme(touch=True)   # bigger grab handles/buttons/scrollbars,
                                      # layered on top of any theme (incl. "none")

touch=True is a separate, additive switch, not a fourth theme choice: it
only widens dock/splitter separators and grows touch-target sizes
(buttons, checkboxes, scrollbars, form fields), it never changes colors --
so it composes with "none" (native colors, just bigger hit targets) the
same way it does with "dark"/"light". See eco.widgets.desktop_app's
--touch CLI flag / EcoDesktopApp(touch=...).

For eco.widgets.camserver_panel_qt / camserver_stream_qt: pass
theme="dark"/"light" to make_camserver_panel_qt(...)/make_camserver_stream_qt(...).
Since each viewer is a separate OS process (see camserver_panel_qt's
module docstring), the panel's choice is propagated to every spawned
viewer subprocess via the ECO_QT_THEME environment variable so the whole
panel-plus-viewers picture stays visually consistent -- apply_modern_theme
records its resolved choice there, and a subprocess that doesn't get an
explicit --theme falls back to reading it. ECO_QT_TOUCH does the same for
touch=True.
"""
import os
import re
import subprocess
import threading

from qtpy import QtCore, QtGui, QtWidgets

ENV_VAR = "ECO_QT_THEME"
TOUCH_ENV_VAR = "ECO_QT_TOUCH"

# qt-material's default dark/light themes are teal-accented -- these
# approximate that exact palette so the fallback (no qt-material
# installed) still reads as "the same theme", not a different one.
_DARK_QSS = """
QWidget { background-color: #1e2124; color: #e6e6e6; font-size: 10.5pt; }
QMainWindow, QDialog { background-color: #1e2124; }
QToolBar { background: #252a2e; border: none; spacing: 6px; padding: 4px; }
QToolButton { background-color: transparent; border: none; padding: 5px 10px; border-radius: 3px; }
QToolButton:hover { background-color: #2d3339; }
QPushButton {
    background-color: transparent; border: 1px solid #00e5c3; border-radius: 3px;
    padding: 5px 14px; color: #00e5c3; font-weight: 500;
}
QPushButton:hover { background-color: rgba(0, 229, 195, 0.12); }
QPushButton:pressed { background-color: rgba(0, 229, 195, 0.25); }
QPushButton:checked {
    background-color: #00bfa5; border-color: #00bfa5; color: #0d1117;
}
QPushButton:disabled { border-color: #454a54; color: #6b7078; }
QLabel { background: transparent; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: transparent; border: none; border-bottom: 2px solid #454a54;
    border-radius: 0px; padding: 4px 4px; selection-background-color: #00bfa5;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-bottom-color: #00e5c3; }
QComboBox QAbstractItemView {
    background-color: #252a2e; border: 1px solid #00e5c3; selection-background-color: #00bfa5;
    selection-color: #0d1117;
}
QGroupBox { border: 1px solid #333941; border-radius: 4px; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #00e5c3; }
QDockWidget { titlebar-close-icon: none; }
QDockWidget::title {
    background: #252a2e; padding: 6px; border-bottom: 2px solid #00bfa5; font-weight: 500;
}
QTabBar::tab {
    background: #252a2e; padding: 6px 14px; border: none; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { border-bottom: 2px solid #00e5c3; color: #00e5c3; }
QScrollArea { border: none; }
QScrollBar:vertical, QScrollBar:horizontal { background: #1e2124; }
QScrollBar::handle { background: #454a54; border-radius: 4px; min-height: 20px; min-width: 20px; }
QScrollBar::handle:hover { background: #00bfa5; }
QCheckBox, QRadioButton { spacing: 6px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #00bfa5; }
QToolTip { background-color: #252a2e; color: #e6e6e6; border: 1px solid #00e5c3; }
"""

_LIGHT_QSS = """
QWidget { background-color: #fafafa; color: #1a1a1a; font-size: 10.5pt; }
QMainWindow, QDialog { background-color: #fafafa; }
QToolBar { background: #ffffff; border: none; spacing: 6px; padding: 4px; }
QToolButton { background-color: transparent; border: none; padding: 5px 10px; border-radius: 3px; }
QToolButton:hover { background-color: #e0f2f1; }
QPushButton {
    background-color: transparent; border: 1px solid #00897b; border-radius: 3px;
    padding: 5px 14px; color: #00897b; font-weight: 500;
}
QPushButton:hover { background-color: rgba(0, 137, 123, 0.10); }
QPushButton:checked { background-color: #00897b; border-color: #00897b; color: white; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: transparent; border: none; border-bottom: 2px solid #c8ccd0;
    padding: 4px 4px; selection-background-color: #00897b; selection-color: white;
}
QLineEdit:focus, QComboBox:focus { border-bottom-color: #00897b; }
QGroupBox { border: 1px solid #dcdfe4; border-radius: 4px; margin-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #00897b; }
QDockWidget::title { background: #ffffff; padding: 6px; border-bottom: 2px solid #00897b; }
QTabBar::tab { background: #ffffff; padding: 6px 14px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { border-bottom: 2px solid #00897b; color: #00897b; }
QScrollBar::handle { background: #c7cbd1; border-radius: 4px; min-height: 20px; min-width: 20px; }
QScrollBar::handle:hover { background: #00897b; }
"""

_QSS = {"dark": _DARK_QSS, "light": _LIGHT_QSS}

# Additive on top of whatever theme (including "none"/no stylesheet at
# all) -- structural sizing only, no colors changed except the grab-handle
# hover highlight, so it reads correctly whether layered on native colors,
# _DARK_QSS or qt-material's own stylesheet. Sizes are rough physical
# touch-target minimums (~9mm), not just "bigger than default": dock/
# splitter separators are the specific thing reported hard to grab.
_TOUCH_QSS = """
QMainWindow::separator { width: 10px; height: 10px; background: #888888; }
QMainWindow::separator:hover { background: #00bfa5; }
QSplitter::handle { background: #888888; }
QSplitter::handle:horizontal { width: 10px; }
QSplitter::handle:vertical { height: 10px; }
QSplitter::handle:hover { background: #00bfa5; }
QPushButton, QToolButton { min-height: 34px; padding: 8px 16px; }
QCheckBox::indicator, QRadioButton::indicator { width: 22px; height: 22px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { min-height: 30px; padding: 5px 6px; }
QScrollBar:vertical { width: 22px; }
QScrollBar:horizontal { height: 22px; }
QScrollBar::handle { min-height: 32px; min-width: 32px; border-radius: 6px; }
QTabBar::tab { padding: 10px 18px; }
QDockWidget::title { padding: 10px; }
QAbstractItemView::item { padding: 6px; }
"""


AUTO = "auto"
NATIVE = "none"
# the choices offered in eco desktop's Help -> Appearance menu, in order
MODES = (AUTO, "light", "dark", NATIVE)
MODE_LABELS = {
    AUTO: "Follow System",
    "light": "Light",
    "dark": "Dark",
    NATIVE: "Native (No Skin)",
}


def _run_quiet(cmd, timeout):
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _scheme_from_qt():
    """Qt >= 6.5 knows the platform's color scheme itself (Qt 5 does not)."""
    app = QtWidgets.QApplication.instance()
    scheme_enum = getattr(QtCore.Qt, "ColorScheme", None)
    if app is None or scheme_enum is None:
        return None
    try:
        scheme = app.styleHints().colorScheme()
    except Exception:
        return None
    if scheme == scheme_enum.Dark:
        return "dark"
    if scheme == scheme_enum.Light:
        return "light"
    return None


def _scheme_from_portal(timeout):
    """freedesktop settings portal (GNOME, KDE, ... on Wayland and X11):
    org.freedesktop.appearance color-scheme, 0 = no preference, 1 = dark,
    2 = light."""
    out = _run_quiet(
        [
            "gdbus", "call", "--session",
            "--timeout", str(max(1, int(round(timeout)))),
            "--dest", "org.freedesktop.portal.Desktop",
            "--object-path", "/org/freedesktop/portal/desktop",
            "--method", "org.freedesktop.portal.Settings.Read",
            "org.freedesktop.appearance", "color-scheme",
        ],
        timeout,
    )
    if not out:
        return None
    match = re.search(r"uint32\s+(\d+)", out)
    if not match:
        return None
    return {1: "dark", 2: "light"}.get(int(match.group(1)))


def _scheme_from_gsettings(timeout):
    """GNOME without a portal: color-scheme ('prefer-dark'), else a GTK
    theme whose name says dark (Adwaita-dark, Yaru-dark, ...)."""
    scheme = _run_quiet(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], timeout
    )
    if scheme:
        if "prefer-dark" in scheme:
            return "dark"
        if "prefer-light" in scheme:
            return "light"
    gtk = _run_quiet(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], timeout)
    if gtk and "dark" in gtk.lower():
        return "dark"
    return None


def _scheme_from_env():
    gtk = os.environ.get("GTK_THEME", "")
    if gtk:
        return "dark" if "dark" in gtk.lower() else "light"
    return None


def detect_system_scheme(timeout=1.0):
    """The desktop's light/dark preference: "dark", "light", or None if
    nothing says. Asks, in order: Qt itself (Qt >= 6.5 only), the
    freedesktop settings portal, gsettings, $GTK_THEME. Runs small
    subprocesses (each bounded by `timeout`), so don't call it from a Qt
    slot -- SystemSchemeWatcher does it on a worker thread.

    "System" means the machine this process runs on: over ssh -X the
    console's own desktop settings are asked (usually: none, so None), not
    the laptop showing the window."""
    for probe in (
        _scheme_from_qt,
        lambda: _scheme_from_portal(timeout),
        lambda: _scheme_from_gsettings(timeout),
        _scheme_from_env,
    ):
        try:
            scheme = probe()
        except Exception:
            scheme = None
        if scheme in ("dark", "light"):
            return scheme
    return None


# the application's own style and palette before eco first skinned it --
# what "Native" goes back to when switched to at runtime
_native = {}


def _remember_native(app):
    if "style" not in _native:
        _native["style"] = app.style().objectName()
        _native["palette"] = QtGui.QPalette(app.palette())


def native_palette_is_dark():
    """Whether the unskinned palette (the platform theme's, e.g. KDE's) is
    dark -- the last resort for "Follow System" when nothing else says."""
    app = QtWidgets.QApplication.instance()
    palette = _native.get("palette")
    if palette is None:
        if app is None:
            return False
        palette = app.palette()
    return palette.color(QtGui.QPalette.Window).lightness() < 128


def resolve_mode(mode, system_scheme=None):
    """A desktop Appearance mode ("auto" | "light" | "dark" | "none") ->
    the theme to apply ("dark" | "light" | None for native). "auto" takes
    `system_scheme` (from detect_system_scheme), and if that's None the
    lightness of the native palette. Pure apart from that palette read."""
    if mode in ("dark", "light"):
        return mode
    if mode == AUTO:
        if system_scheme in ("dark", "light"):
            return system_scheme
        return "dark" if native_palette_is_dark() else "light"
    return None


def resolve_theme(theme=None):
    """theme: "dark" | "light" | "auto" | None -> effective theme name. None means
    "defer to ECO_QT_THEME in the environment" (so a viewer subprocess
    picks up the panel's choice automatically without needing an explicit
    --theme of its own), falling back to no theme (native OS style) if
    that's unset too; "none" is native explicitly. "auto" follows the desktop's light/dark preference
    (detect_system_scheme). Unit tested independently of Qt."""
    if theme == AUTO:
        return resolve_mode(AUTO, detect_system_scheme())
    if theme == NATIVE:
        return None
    if theme not in ("dark", "light"):
        theme = os.environ.get(ENV_VAR)
    return theme if theme in ("dark", "light") else None


def resolve_touch(touch=None):
    """touch: True/False/None -> effective touch-mode bool, mirroring
    resolve_theme's "defer to the environment" convention: None means
    "defer to ECO_QT_TOUCH" (so a viewer subprocess spawned by a
    touch-enabled panel picks up the same choice automatically), falling
    back to False (off) if that's unset too. Pure function."""
    if touch is None:
        return os.environ.get(TOUCH_ENV_VAR) == "1"
    return bool(touch)


def _apply_qt_material(app, theme):
    """Try the real qt-material package first. Returns True if applied."""
    try:
        import qt_material
    except ImportError:
        return False
    qt_material.apply_stylesheet(app, theme="dark_teal.xml" if theme == "dark" else "light_teal.xml")
    return True


def apply_modern_theme(theme=None, touch=None):
    """Apply (or, with an unresolvable theme, clear) the skin on the
    current QApplication. Call once, right after creating/obtaining the
    QApplication instance -- a no-op if there isn't one yet. Also records
    the resolved choices in os.environ[ECO_QT_THEME]/[ECO_QT_TOUCH] so any
    subprocess this process later spawns inherits the same look by
    default. Uses the real qt-material package for theme if installed,
    else a close hand-built approximation of its default teal theme (see
    module docstring).

    touch=True (or ECO_QT_TOUCH=1 in the environment) layers _TOUCH_QSS
    (bigger dock/splitter grab handles, buttons, checkboxes, scrollbars --
    see its own docstring) on top of whatever `theme` resolves to,
    appended last so it wins on the sizing properties it sets regardless
    of which base stylesheet (native/hand-built/qt-material) is under it.
    Still returns just the resolved theme (unchanged contract) -- nothing
    today reads apply_modern_theme's return value, and resolve_touch is
    there directly for anything that later needs the touch choice too."""
    resolved = resolve_theme(theme)
    resolved_touch = resolve_touch(touch)
    os.environ[ENV_VAR] = resolved or ""
    os.environ[TOUCH_ENV_VAR] = "1" if resolved_touch else ""

    app = QtWidgets.QApplication.instance()
    if app is None:
        return resolved
    _remember_native(app)
    if resolved is None:
        app.setStyleSheet(_TOUCH_QSS if resolved_touch else "")
        # back from a skin switched on earlier in this process
        if _native.get("style") and app.style().objectName() != _native["style"]:
            app.setStyle(_native["style"])
        app.setPalette(_native["palette"])
        return resolved
    if _apply_qt_material(app, resolved):
        if resolved_touch:
            app.setStyleSheet(app.styleSheet() + _TOUCH_QSS)
        return resolved
    app.setStyle("Fusion")
    app.setStyleSheet(_QSS[resolved] + (_TOUCH_QSS if resolved_touch else ""))
    return resolved


class SystemSchemeWatcher(QtCore.QObject):
    """Polls detect_system_scheme on a worker thread every `interval` s
    and emits `changed("dark"|"light"|"")` (on the GUI thread) when the
    answer differs from the last one -- "" for "nothing says". Polling,
    not D-Bus signals: it covers gsettings and $GTK_THEME too, and costs a
    few ms of subprocess every few seconds, off the GUI thread."""

    changed = QtCore.Signal(str)

    def __init__(self, interval=5.0, parent=None, initial=None):
        super().__init__(parent)
        self.scheme = initial
        self._result = None
        self._busy = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(int(interval * 1000))
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()
        self._tick()

    def stop(self):
        self._timer.stop()

    def _probe(self):
        try:
            self._result = detect_system_scheme() or ""
        finally:
            self._busy = False

    def _tick(self):
        # a Qt slot: nothing may escape
        try:
            result, self._result = self._result, None
            if result is not None and result != (self.scheme or ""):
                self.scheme = result or None
                self.changed.emit(result)
            if not self._busy:
                self._busy = True
                threading.Thread(target=self._probe, name="eco-scheme-probe", daemon=True).start()
        except RuntimeError:  # deleted while a probe ran
            pass
