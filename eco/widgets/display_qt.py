"""
Qt based window to view display items and (where supported) set targets.
This is the non-notebook counterpart of eco.widgets.display_widget: same
layout and behavior (up/down tweak buttons relative to the real current
value, absolute-value entry committed on Enter/focus-out, no separate "Set"
button), shown in a Qt window instead of an ipywidgets box.

Uses qtpy so it works with whichever Qt binding is installed (PyQt5/PyQt6/
PySide2/PySide6).

Usage (blocking, e.g. plain python script):
    from eco.widgets.display_qt import make_assembly_qt_window
    gui = make_assembly_qt_window(my_assembly, poll_interval=1.0, auto_start=False)
    gui.run()   # blocks until the window is closed

Usage (non-blocking, e.g. terminal IPython session):
    gui = make_assembly_qt_window(my_assembly, poll_interval=1.0)  # auto_start=True by default
    ...
    gui.stop()   # closes the window

If IPython is running with a GUI event loop already active (commonly `qt`/
`qt5`/`qt6`, e.g. because caqtdm or another Qt tool is in use), this reuses
that same loop instead of fighting it. If no loop is active yet, it enables
one via `ip.enable_gui("qt")`. If a *different*, incompatible loop is active
(e.g. `tk`), a non-blocking window isn't possible, so it falls back to a
blocking `run()`.
"""
from qtpy import QtWidgets, QtCore
from typing import Any

_app_ref = None  # keep a strong reference to any QApplication we create ourselves

# Try to import types for isinstance checks if available.
try:
    from eco import Adjustable, Detector
except Exception:
    Adjustable = object
    Detector = object


def _label_of(item: Any, assembly=None) -> str:
    try:
        if hasattr(item, "alias") and hasattr(item.alias, "get_full_name"):
            return (
                item.alias.get_full_name(base=assembly)
                if assembly is not None
                else item.alias.get_full_name()
            )
    except Exception:
        pass
    try:
        if hasattr(item, "name"):
            return str(item.name)
    except Exception:
        pass
    return str(item)


def _coerce_like(value: Any, reference: Any):
    """Coerce a string entry value to the type of `reference` (best effort)."""
    if isinstance(reference, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(reference, int):
        return int(float(value))
    if isinstance(reference, float):
        return float(value)
    return value


def _default_step_for(value: Any):
    if isinstance(value, int) and not isinstance(value, bool):
        return 1
    return 0.1


def _flash_error(widget):
    try:
        widget.setStyleSheet("background-color: #ffb3b3;")
        QtCore.QTimer.singleShot(1200, lambda: widget.setStyleSheet(""))
    except Exception:
        pass


class DisplayQt:
    def __init__(self, assembly, poll_interval: float = 1.0, auto_start: bool = True):
        self.assembly = assembly
        self.poll_interval = poll_interval
        self.window = None
        self._timer = None
        self._entries = []  # list of dicts: item, value_label

        if auto_start:
            self.start()

    def _get_display_items(self):
        try:
            return list(self.assembly.display_collection())
        except Exception:
            try:
                return list(
                    self.assembly.status_collection.get_list(selection="display")
                )
            except Exception:
                return []

    def _build_row(self, item, layout):
        name = _label_of(item, assembly=self.assembly)
        try:
            cur = item.get_current_value()
        except Exception:
            cur = "<error>"

        row = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel(name)
        name_label.setFixedWidth(200)
        row.addWidget(name_label)

        value_label = QtWidgets.QLabel(str(cur))
        value_label.setFixedWidth(140)
        row.addWidget(value_label)

        # Detector (non-Adjustable): read-only
        if isinstance(item, Detector) and not isinstance(item, Adjustable):
            row.addWidget(QtWidgets.QLabel("read-only (Detector)"))

        # Adjustable: step field + up/down + absolute entry (commits on Enter/focus-out)
        elif isinstance(item, Adjustable):
            is_plain_scalar = not isinstance(cur, (list, dict, bytes, bytearray))

            step_edit = QtWidgets.QLineEdit(str(_default_step_for(cur)))
            step_edit.setFixedWidth(60)
            row.addWidget(step_edit)

            if is_plain_scalar:
                input_edit = QtWidgets.QLineEdit(str(cur))
                input_edit.setFixedWidth(100)

                def _apply_absolute(it=item, vl=value_label, ie=input_edit, ref=cur):
                    try:
                        newval = _coerce_like(ie.text(), ref)
                        r = it.set_target_value(newval)
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        new_current = it.get_current_value()
                        vl.setText(str(new_current))
                        ie.setText(str(new_current))
                    except Exception:
                        _flash_error(ie)

                input_edit.editingFinished.connect(_apply_absolute)
            else:
                input_edit = QtWidgets.QLabel("n/a")
                input_edit.setFixedWidth(100)

            def _make_tweak(sign, it=item, vl=value_label, se=step_edit,
                             ie=input_edit, plain=is_plain_scalar):
                def _on_click():
                    try:
                        step = _coerce_like(se.text(), _default_step_for(cur))
                        base = it.get_current_value()
                        newval = base + sign * step
                        r = it.set_target_value(newval)
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        new_current = it.get_current_value()
                        vl.setText(str(new_current))
                        if plain:
                            ie.setText(str(new_current))
                    except Exception:
                        _flash_error(ie if isinstance(ie, QtWidgets.QLineEdit) else vl)

                return _on_click

            up_btn = QtWidgets.QPushButton("▲")
            up_btn.setFixedWidth(30)
            up_btn.clicked.connect(_make_tweak(1))
            down_btn = QtWidgets.QPushButton("▼")
            down_btn.setFixedWidth(30)
            down_btn.clicked.connect(_make_tweak(-1))
            row.addWidget(up_btn)
            row.addWidget(down_btn)

            row.addWidget(input_edit)

        # Fallback: has set_target_value but not recognized as Adjustable
        elif hasattr(item, "set_target_value") and callable(
            getattr(item, "set_target_value")
        ):
            input_edit = QtWidgets.QLineEdit(str(cur))

            def _apply(it=item, vl=value_label, ie=input_edit, ref=cur):
                try:
                    newval = _coerce_like(ie.text(), ref)
                    r = it.set_target_value(newval)
                    try:
                        if hasattr(r, "wait"):
                            r.wait(timeout=5)
                    except Exception:
                        pass
                    new_current = it.get_current_value()
                    vl.setText(str(new_current))
                    ie.setText(str(new_current))
                except Exception:
                    _flash_error(ie)

            input_edit.editingFinished.connect(_apply)
            row.addWidget(input_edit)

        else:
            row.addWidget(QtWidgets.QLabel("—"))

        row.addStretch(1)
        layout.addLayout(row)
        self._entries.append({"item": item, "value_label": value_label})

    def _build_window(self):
        global _app_ref
        if QtWidgets.QApplication.instance() is None:
            # should normally already exist (created by IPython's qt inputhook,
            # or by run() below), but guard against it missing regardless
            _app_ref = QtWidgets.QApplication([])

        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle(
            f"Assembly Display - {getattr(self.assembly, 'name', '')}"
        )
        # don't let closing our window quit a shared QApplication/event loop
        self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)

        outer = QtWidgets.QVBoxLayout(self.window)

        header = QtWidgets.QHBoxLayout()
        for text, width in (("name", 200), ("current", 140), ("control", 0)):
            lbl = QtWidgets.QLabel(f"<b>{text}</b>")
            if width:
                lbl.setFixedWidth(width)
            header.addWidget(lbl)
        header.addStretch(1)
        outer.addLayout(header)

        self._entries = []
        for item in self._get_display_items():
            self._build_row(item, outer)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.stop)
        outer.addWidget(close_btn)

        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(int(self.poll_interval * 1000))

        self.window.show()

    def _poll(self):
        if self.window is None:
            return
        for ent in self._entries:
            try:
                val = ent["item"].get_current_value()
                ent["value_label"].setText(str(val))
            except Exception:
                pass

    def run(self):
        """Build and run the window with a blocking Qt event loop. Use this
        when there is no GUI event-loop integration available to pump the
        window for you (e.g. a plain python script, or a terminal with an
        incompatible GUI loop already active)."""
        app = QtWidgets.QApplication.instance()
        created_app = app is None
        if created_app:
            app = QtWidgets.QApplication([])
        if self.window is None:
            self._build_window()
        if created_app:
            app.exec_()

    def start(self):
        """
        Show the window without blocking. Inside an IPython terminal
        session this reuses (or enables) IPython's Qt event-loop
        integration, which pumps the window between prompts on the main
        thread. Outside of IPython, or if a different/incompatible GUI
        loop is already active, this falls back to the blocking `run()`.
        """
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
        if active in (None,):
            try:
                ip.enable_gui("qt")
            except Exception:
                pass
        elif active not in ("qt", "qt4", "qt5", "qt6"):
            print(
                f"eco widget: a different GUI event loop ('{active}') is already "
                "active in this IPython session, so the window can't be pumped "
                "non-blockingly alongside it. Showing it in blocking mode instead "
                "(closing the window returns control) - re-run after '%gui' "
                "(no arguments) to disable the current loop if you want the "
                "non-blocking window."
            )
            self.run()
            return

        self._build_window()

    def stop(self):
        """Close the window."""
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None
        if self._timer is not None:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None


def make_assembly_qt_window(assembly, poll_interval: float = 1.0, auto_start: bool = True):
    """Convenience factory, mirrors make_assembly_widget's signature."""
    return DisplayQt(assembly, poll_interval=poll_interval, auto_start=auto_start)
