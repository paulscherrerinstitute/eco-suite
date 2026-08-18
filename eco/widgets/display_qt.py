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
import enum
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from qtpy import QtWidgets, QtCore
from typing import Any


def _format_value(value):
    """Show enum-valued readbacks by their label (describer) rather than the
    underlying integer."""
    if isinstance(value, enum.Enum):
        return value.name
    return str(value)


def _enum_options(item, cur):
    """Return the ordered list of enum label strings for an enum-enabled item
    (anything satisfying the eco.elements.protocols.AdjustableEnum/DetectorEnum
    protocol, e.g. AdjustablePvEnum / eco.elements.adjustable.AdjustableEnum),
    or None if it isn't enum-enabled."""
    if isinstance(item, (AdjustableEnumProtocol, DetectorEnumProtocol)):
        strs = getattr(item, "enum_strs", None)
        if strs:
            try:
                return [str(s) for s in strs]
            except Exception:
                pass
    # fall back to the enum class of the current value (e.g. vacuum Valve,
    # which composes ValveState from two plain booleans rather than exposing
    # enum_strs itself)
    if isinstance(cur, enum.Enum):
        members = sorted(
            type(cur).__members__.items(), key=lambda kv: kv[1].value
        )
        return [name for name, _ in members]
    return None

_app_ref = None  # keep a strong reference to any QApplication we create ourselves

_PREFETCH_ERROR = object()  # sentinel: a prefetched read raised


def _prefetch_values(items, max_workers=8):
    """Read all initial values concurrently so building the window for a large
    assembly is bounded by the slowest single readback rather than their sum.
    Returns {id(item): value_or__PREFETCH_ERROR}. Never raises."""
    values = {}
    items = list(items)
    if not items:
        return values

    def _read(it):
        return it.get_current_value()

    try:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
            futures = {ex.submit(_read, it): it for it in items}
            for fut, it in futures.items():
                try:
                    values[id(it)] = fut.result()
                except Exception:
                    values[id(it)] = _PREFETCH_ERROR
    except Exception:
        pass
    return values

# Adaptive polling: an item whose get_current_value() is slow to respond gets
# polled less often, so slow/blocking readbacks don't starve the GUI and the
# fast items. An item's poll interval becomes ~ max(base, duration * FACTOR),
# capped at MAX_INTERVAL. Fast items stay at the base interval. On Qt the poll
# also runs off the GUI thread, so a slow readback never freezes the window.
_POLL_SLOWDOWN_FACTOR = 10.0
_POLL_MAX_INTERVAL = 30.0


class _PollBridge(QtCore.QObject):
    """Marshals value updates from the background poll thread to the GUI
    thread (emitting a Qt signal across threads is thread-safe; the connected
    slot then runs on the GUI thread where widgets may be touched)."""

    result = QtCore.Signal(int, str)
    trigger_failed = QtCore.Signal(object)  # the button widget to flash

# Try to import types for isinstance checks if available.
try:
    from eco import Adjustable, Detector
    from eco import AdjustableEnum as AdjustableEnumProtocol
    from eco import DetectorEnum as DetectorEnumProtocol
    from eco.elements.assembly import Assembly
    from eco.elements.adjustable import AdjustableTrigger
except Exception:
    Adjustable = object
    Detector = object

    class AdjustableEnumProtocol:
        pass

    class DetectorEnumProtocol:
        pass

    Assembly = object

    class AdjustableTrigger:
        pass


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


def _is_tweakable(value: Any) -> bool:
    """Only plain numeric values support +/- step tweaking; e.g. strings don't."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _flash_error(widget):
    try:
        widget.setStyleSheet("background-color: #ffb3b3;")
        QtCore.QTimer.singleShot(1200, lambda: widget.setStyleSheet(""))
    except Exception:
        pass


class DisplayQt:
    def __init__(
        self,
        assembly,
        poll_interval: float = 1.0,
        auto_start: bool = True,
        show_hidden: bool = False,
    ):
        self.assembly = assembly
        self.poll_interval = poll_interval
        self.show_hidden = show_hidden
        self.window = None
        self._entries = []  # list of dicts: item, value_label, active, interval, next_due
        self._child_windows = {}  # id(child_assembly) -> DisplayQt shown in its own window
        self._bridge = None
        self._poll_thread = None
        self._stop_event = threading.Event()
        self._prefetched = {}
        self._memory_browser = None  # lazily-built "memories" sub-window, see _open_memory_browser

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

    def _get_hidden_items(self):
        display_items = self._get_display_items()
        try:
            all_items = list(self.assembly.status_collection.get_list())
        except Exception:
            return []
        return [it for it in all_items if it not in display_items]

    def _build_row(self, item, layout, active=True):
        name = _label_of(item, assembly=self.assembly)

        if isinstance(item, AdjustableTrigger):
            # no value to show/poll -- see AdjustableTrigger's docstring
            row = QtWidgets.QHBoxLayout()
            name_label = QtWidgets.QLabel(name)
            name_label.setFixedWidth(200)
            row.addWidget(name_label)
            row.addWidget(QtWidgets.QLabel(""))  # keep the value column aligned
            btn = QtWidgets.QPushButton(item.button_label or "▶ Trigger")
            if item.doc:
                btn.setToolTip(item.doc)

            def _on_click(checked=False, it=item, b=btn):
                def _run():
                    try:
                        it.trigger()
                    except Exception:
                        # _flash_error touches a widget's style -- not safe
                        # from this background thread, so marshal it back
                        # to the GUI thread via the bridge signal
                        self._bridge.trigger_failed.emit(b)

                threading.Thread(target=_run, daemon=True).start()

            btn.clicked.connect(_on_click)
            row.addWidget(btn)
            row.addStretch(1)
            layout.addLayout(row)
            return  # not polled -- nothing to register in self._entries

        prefetched = getattr(self, "_prefetched", {})
        if id(item) in prefetched:
            cur = prefetched[id(item)]
            if cur is _PREFETCH_ERROR:
                cur = "<error>"
        else:
            try:
                cur = item.get_current_value()
            except Exception:
                cur = "<error>"

        row = QtWidgets.QHBoxLayout()
        if isinstance(item, Assembly):
            name_label = QtWidgets.QPushButton(name)
            name_label.setFlat(True)
            name_label.setStyleSheet(
                "text-align:left; border:none; color:#2a6fdb; text-decoration: underline;"
            )
            name_label.setCursor(QtCore.Qt.PointingHandCursor)
            name_label.clicked.connect(
                lambda checked=False, it=item: self._open_child_window(it)
            )
        else:
            name_label = QtWidgets.QLabel(name)
        name_label.setFixedWidth(200)
        row.addWidget(name_label)

        value_label = QtWidgets.QLabel(_format_value(cur))
        value_label.setFixedWidth(140)
        row.addWidget(value_label)
        combo_ref = {"combo": None}  # set below for enum rows; polled by _apply_update

        # Detector (non-Adjustable): read-only
        if isinstance(item, Detector) and not isinstance(item, Adjustable):
            row.addWidget(QtWidgets.QLabel("read-only (Detector)"))

        # Adjustable: step field + up/down + absolute entry (commits on Enter/focus-out)
        elif isinstance(item, Adjustable):
            original_value = cur
            enum_opts = _enum_options(item, cur)
            changer_ref = {"changer": None}

            if enum_opts is not None:
                # ENUM adjustable: dropdown selector, current readback preselected
                combo = QtWidgets.QComboBox()
                combo.addItems(enum_opts)
                cur_label = _format_value(cur)
                if cur_label in enum_opts:
                    combo.setCurrentText(cur_label)
                combo.setFixedWidth(140)
                combo_ref["combo"] = combo

                def _enum_set(label):
                    try:
                        r = item.set_target_value(label)
                        changer_ref["changer"] = r
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            new_cur = item.get_current_value()
                        except Exception:
                            new_cur = None
                        if new_cur is not None:
                            lbl = _format_value(new_cur)
                            value_label.setText(lbl)
                            idx = combo.findText(lbl)
                            if idx >= 0:
                                combo.blockSignals(True)
                                combo.setCurrentIndex(idx)
                                combo.blockSignals(False)
                    except Exception:
                        _flash_error(combo)

                def _on_activated(index=0):
                    _enum_set(combo.currentText())

                def _on_stop(checked=False):
                    changer = changer_ref.get("changer")
                    if changer is not None and hasattr(changer, "stop"):
                        try:
                            changer.stop()
                        except Exception:
                            pass

                def _on_reset(checked=False):
                    _enum_set(
                        original_value.name
                        if isinstance(original_value, enum.Enum)
                        else original_value
                    )

                combo.activated.connect(_on_activated)
                stop_btn = QtWidgets.QPushButton("🛑")
                stop_btn.setFixedWidth(30)
                stop_btn.setToolTip("Stop the current move")
                stop_btn.clicked.connect(_on_stop)
                reset_btn = QtWidgets.QPushButton("↺")
                reset_btn.setFixedWidth(30)
                reset_btn.setToolTip("Reset to the value from when this widget was opened")
                reset_btn.clicked.connect(_on_reset)
                row.addWidget(combo)
                row.addWidget(stop_btn)
                row.addWidget(reset_btn)

            else:
                is_plain_scalar = not isinstance(cur, (list, dict, bytes, bytearray))
                tweakable = _is_tweakable(cur)

                if tweakable:
                    step_edit = QtWidgets.QLineEdit(str(_default_step_for(cur)))
                    step_edit.setFixedWidth(60)
                    row.addWidget(step_edit)

                if is_plain_scalar:
                    input_edit = QtWidgets.QLineEdit(str(cur))
                    input_edit.setFixedWidth(100)

                    def _apply_absolute(it=item, vl=value_label, ie=input_edit, ref=cur,
                                         cref=changer_ref):
                        try:
                            newval = _coerce_like(ie.text(), ref)
                            r = it.set_target_value(newval)
                            cref["changer"] = r
                            try:
                                if hasattr(r, "wait"):
                                    r.wait(timeout=5)
                            except Exception:
                                pass
                            new_current = it.get_current_value()
                            vl.setText(_format_value(new_current))
                            ie.setText(str(new_current))
                        except Exception:
                            _flash_error(ie)

                    input_edit.editingFinished.connect(_apply_absolute)
                else:
                    input_edit = QtWidgets.QLabel("n/a")
                    input_edit.setFixedWidth(100)

                if tweakable:
                    def _make_tweak(sign, it=item, vl=value_label, se=step_edit,
                                     ie=input_edit, plain=is_plain_scalar, cref=changer_ref):
                        def _on_click():
                            try:
                                step = _coerce_like(se.text(), _default_step_for(cur))
                                base = it.get_current_value()
                                newval = base + sign * step
                                r = it.set_target_value(newval)
                                cref["changer"] = r
                                try:
                                    if hasattr(r, "wait"):
                                        r.wait(timeout=5)
                                except Exception:
                                    pass
                                new_current = it.get_current_value()
                                vl.setText(_format_value(new_current))
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

                def _on_stop(checked=False, it=item, cref=changer_ref):
                    changer = cref.get("changer")
                    if changer is not None and hasattr(changer, "stop"):
                        try:
                            changer.stop()
                        except Exception:
                            pass

                def _on_reset(checked=False, it=item, vl=value_label, ie=input_edit,
                              plain=is_plain_scalar, ref=original_value, cref=changer_ref):
                    try:
                        r = it.set_target_value(ref)
                        cref["changer"] = r
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        new_current = it.get_current_value()
                        vl.setText(_format_value(new_current))
                        if plain:
                            ie.setText(str(new_current))
                    except Exception:
                        if isinstance(ie, QtWidgets.QLineEdit):
                            _flash_error(ie)

                stop_btn = QtWidgets.QPushButton("🛑")
                stop_btn.setFixedWidth(30)
                stop_btn.setToolTip("Stop the current move")
                stop_btn.clicked.connect(_on_stop)
                reset_btn = QtWidgets.QPushButton("↺")
                reset_btn.setFixedWidth(30)
                reset_btn.setToolTip("Reset to the value from when this widget was opened")
                reset_btn.clicked.connect(_on_reset)
                row.addWidget(stop_btn)
                row.addWidget(reset_btn)

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
        self._entries.append(
            {
                "item": item,
                "value_label": value_label,
                "combo": combo_ref["combo"],
                "active": active,
                "interval": self.poll_interval,
                "next_due": 0.0,  # monotonic time; 0 => poll on first pass
            }
        )

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

        # everything except Close lives in a scroll area, so the window
        # doesn't grow past the screen when an assembly has many items
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(scroll_content)

        header = QtWidgets.QHBoxLayout()
        for text, width in (("name", 200), ("current", 140), ("control", 0)):
            lbl = QtWidgets.QLabel(f"<b>{text}</b>")
            if width:
                lbl.setFixedWidth(width)
            header.addWidget(lbl)
        header.addStretch(1)
        content_layout.addLayout(header)

        # created up front (before any row is built) so AdjustableTrigger
        # rows can use it too: a trigger's action runs on a background
        # thread (see _build_row) so a blocking hardware call never
        # freezes the window, and touching a widget's style from that
        # thread on failure isn't safe -- this bridge marshals it back
        self._bridge = _PollBridge()
        self._bridge.result.connect(self._apply_update)
        self._bridge.trigger_failed.connect(_flash_error)

        display_items = self._get_display_items()
        hidden_items = self._get_hidden_items()
        # read every initial value concurrently up front, so opening a large
        # assembly is bounded by the slowest single readback, not their sum
        self._prefetched = _prefetch_values(list(display_items) + list(hidden_items))

        self._entries = []
        for item in display_items:
            self._build_row(item, content_layout)

        if hidden_items:
            toggle_row = QtWidgets.QHBoxLayout()
            toggle_btn = QtWidgets.QPushButton(
                "hide hidden" if self.show_hidden else "expand hidden"
            )
            toggle_row.addWidget(toggle_btn)
            toggle_row.addStretch(1)
            content_layout.addLayout(toggle_row)

            hidden_container = QtWidgets.QWidget()
            hidden_container.setStyleSheet(
                "background-color: #e0e0e0; border-radius: 4px;"
            )
            hidden_layout = QtWidgets.QVBoxLayout(hidden_container)
            hidden_start = len(self._entries)
            for item in hidden_items:
                self._build_row(item, hidden_layout, active=self.show_hidden)
            hidden_entries = self._entries[hidden_start:]
            hidden_container.setVisible(self.show_hidden)
            content_layout.addWidget(hidden_container)

            def _toggle_hidden(
                checked=False, btn=toggle_btn, box=hidden_container,
                entries=hidden_entries,
            ):
                showing = box.isVisible()
                box.setVisible(not showing)
                btn.setText("expand hidden" if showing else "hide hidden")
                # only poll expanded items, so hidden ones don't consume resources
                for ent in entries:
                    ent["active"] = not showing
                    if not showing:
                        ent["next_due"] = 0.0  # refresh immediately on expand

            toggle_btn.clicked.connect(_toggle_hidden)

        content_layout.addStretch(1)
        scroll_area.setWidget(scroll_content)
        outer.addWidget(scroll_area)

        bottom_row = QtWidgets.QHBoxLayout()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.stop)
        bottom_row.addWidget(close_btn)
        if hasattr(self.assembly, "memory"):
            memories_btn = QtWidgets.QPushButton("memories")
            memories_btn.clicked.connect(self._open_memory_browser)
            bottom_row.addWidget(memories_btn)
        bottom_row.addStretch(1)
        outer.addLayout(bottom_row)

        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))

        # poll off the GUI thread so a slow readback never freezes the window;
        # results are marshalled back to the GUI thread via the bridge signal
        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self._cap_window_size()
        self.window.show()

    def _cap_window_size(self):
        """Keep the window from growing past the screen; the scroll area
        (added above) takes over once content exceeds this."""
        try:
            self.window.adjustSize()
            screen = QtWidgets.QApplication.primaryScreen()
            max_h = int(screen.availableGeometry().height() * 0.8) if screen else 800
            hint = self.window.sizeHint()
            target_h = min(hint.height(), max_h)
            self.window.resize(hint.width(), target_h)
        except Exception:
            pass

    def _open_child_window(self, child_assembly):
        existing = self._child_windows.get(id(child_assembly))
        if existing is not None and existing.window is not None:
            existing.window.raise_()
            existing.window.activateWindow()
            return
        child = DisplayQt(child_assembly, poll_interval=self.poll_interval, auto_start=False)
        child._build_window()
        self._child_windows[id(child_assembly)] = child

    def _open_memory_browser(self, checked=False):
        if self._memory_browser is not None and self._memory_browser.window is not None:
            self._memory_browser.window.raise_()
            self._memory_browser.window.activateWindow()
            return
        from eco.widgets.memory_widget import make_memory_browser_qt

        self._memory_browser = make_memory_browser_qt(self.assembly, parent=None)

    def _apply_update(self, idx, text):
        # runs on the GUI thread (queued signal) - safe to touch widgets here
        try:
            entry = self._entries[idx]
            entry["value_label"].setText(text)
            combo = entry.get("combo")
            if combo is not None:
                # keep the dropdown selection tracking the readback (e.g. a
                # valve settling from a stale/mismatched selection to its
                # true OPEN/CLOSED state) -- blockSignals so this doesn't
                # re-fire _on_activated and issue a spurious write, same
                # pattern _enum_set already uses after a real user-driven set
                found = combo.findText(text)
                if found >= 0 and combo.currentIndex() != found:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(found)
                    combo.blockSignals(False)
        except Exception:
            pass

    def _poll_loop(self):
        # background thread: poll each active item only when due, backing off
        # items whose readback is slow so they don't starve the GUI / fast items
        base = self.poll_interval
        entries = self._entries
        while not self._stop_event.is_set():
            now = time.monotonic()
            next_wakeup = now + base
            for idx, ent in enumerate(entries):
                if self._stop_event.is_set():
                    break
                if not ent.get("active", True):
                    continue
                if now < ent["next_due"]:
                    next_wakeup = min(next_wakeup, ent["next_due"])
                    continue
                t0 = time.monotonic()
                text = None
                try:
                    text = _format_value(ent["item"].get_current_value())
                except Exception:
                    text = None
                duration = time.monotonic() - t0
                ent["interval"] = min(
                    _POLL_MAX_INTERVAL,
                    max(base, duration * _POLL_SLOWDOWN_FACTOR),
                )
                ent["next_due"] = time.monotonic() + ent["interval"]
                if text is not None:
                    self._bridge.result.emit(idx, text)
                next_wakeup = min(next_wakeup, ent["next_due"])
            sleep_for = max(0.05, next_wakeup - time.monotonic())
            if self._stop_event.wait(sleep_for):
                break

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
        """Close the window (and any open child-assembly windows)."""
        for child in list(self._child_windows.values()):
            try:
                child.stop()
            except Exception:
                pass
        self._child_windows.clear()
        if self._memory_browser is not None:
            try:
                self._memory_browser.window.close()
            except Exception:
                pass
            self._memory_browser = None
        # signal the daemon poll thread to exit (no join: it may be mid-readback,
        # and we don't want to block the GUI thread waiting on it)
        self._stop_event.set()
        self._poll_thread = None
        if self.window is not None:
            try:
                self.window.close()
            except Exception:
                pass
            self.window = None


def make_assembly_qt_window(
    assembly,
    poll_interval: float = 1.0,
    auto_start: bool = True,
    show_hidden: bool = False,
):
    """Convenience factory, mirrors make_assembly_widget's signature."""
    return DisplayQt(
        assembly,
        poll_interval=poll_interval,
        auto_start=auto_start,
        show_hidden=show_hidden,
    )
