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

For eco.widgets.camserver_panel_qt / camserver_stream_qt: pass
theme="dark"/"light" to make_camserver_panel_qt(...)/make_camserver_stream_qt(...).
Since each viewer is a separate OS process (see camserver_panel_qt's
module docstring), the panel's choice is propagated to every spawned
viewer subprocess via the ECO_QT_THEME environment variable so the whole
panel-plus-viewers picture stays visually consistent -- apply_modern_theme
records its resolved choice there, and a subprocess that doesn't get an
explicit --theme falls back to reading it.
"""
import os

from qtpy import QtWidgets

ENV_VAR = "ECO_QT_THEME"

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


def resolve_theme(theme=None):
    """theme: "dark" | "light" | None -> effective theme name. None means
    "defer to ECO_QT_THEME in the environment" (so a viewer subprocess
    picks up the panel's choice automatically without needing an explicit
    --theme of its own), falling back to no theme (native OS style) if
    that's unset too. Pure function, unit tested independently of Qt."""
    if theme not in ("dark", "light"):
        theme = os.environ.get(ENV_VAR)
    return theme if theme in ("dark", "light") else None


def _apply_qt_material(app, theme):
    """Try the real qt-material package first. Returns True if applied."""
    try:
        import qt_material
    except ImportError:
        return False
    qt_material.apply_stylesheet(app, theme="dark_teal.xml" if theme == "dark" else "light_teal.xml")
    return True


def apply_modern_theme(theme=None):
    """Apply (or, with an unresolvable theme, clear) the skin on the
    current QApplication. Call once, right after creating/obtaining the
    QApplication instance -- a no-op if there isn't one yet. Also records
    the resolved choice in os.environ[ECO_QT_THEME] so any subprocess
    this process later spawns inherits the same look by default. Uses the
    real qt-material package if installed, else a close hand-built
    approximation of its default teal theme (see module docstring)."""
    resolved = resolve_theme(theme)
    os.environ[ENV_VAR] = resolved or ""

    app = QtWidgets.QApplication.instance()
    if app is None:
        return resolved
    if resolved is None:
        app.setStyleSheet("")
        return resolved
    if _apply_qt_material(app, resolved):
        return resolved
    app.setStyle("Fusion")
    app.setStyleSheet(_QSS[resolved])
    return resolved
