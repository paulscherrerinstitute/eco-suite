"""
PySimpleGUI/Tkinter based GUI for Assembly display items.

Usage (blocking):
    from eco.widgets.display_tk import DisplayTk
    gui = DisplayTk(my_assembly, poll_interval=1.0)
    gui.run()   # runs the GUI event loop (blocking)

Usage (non-blocking from notebook/script):
    gui = DisplayTk(my_assembly, poll_interval=1.0)
    gui.start()      # spawns GUI loop in a background thread
    ...
    gui.stop()       # stops polling and closes window

Behavior:
 - Shows name / current value for each display_collection item.
 - If item is Detector and not Adjustable -> read-only label.
 - If item is Adjustable:
     - numeric -> step field + up/down + value entry + Set button
     - enum (AdjustableEnum) -> Combo with options
     - non-numeric -> text entry + Set button
 - If item supports MonitorableValueUpdate, attempts to register a callback
   via common setter names so value label is updated by callback instead of polling.
"""

import threading
import time
import traceback
from typing import Any, Dict, List, Optional

try:
    import PySimpleGUI as sg
except Exception as e:
    raise RuntimeError("PySimpleGUI is required for this module") from e

# Try to import eco types for isinstance checks
try:
    from eco import Adjustable, Detector, AdjustableEnum, MonitorableValueUpdate
except Exception:  # fall back to object so isinstance checks are safe
    Adjustable = object
    Detector = object
    AdjustableEnum = object
    MonitorableValueUpdate = object


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


def _get_enum_options(item) -> Optional[List]:
    for attr in ("choices", "options", "allowed_values", "values", "enum_values"):
        opts = getattr(item, attr, None)
        if opts:
            try:
                return list(opts)
            except Exception:
                return opts
    for meth in ("get_choices", "get_options", "allowed_values"):
        fn = getattr(item, meth, None)
        if callable(fn):
            try:
                return list(fn())
            except Exception:
                try:
                    return fn()
                except Exception:
                    pass
    return None


class DisplayTk:
    def __init__(self, assembly, poll_interval: float = 1.0, auto_start: bool = False):
        """
        assembly: assembly instance with a display collection (assembly.display_collection() or selection "display")
        poll_interval: seconds between polls for non-monitorable items
        auto_start: if True start the GUI loop in a background thread on init
        """
        self.assembly = assembly
        self.poll_interval = poll_interval
        self.window = None
        self._stop_event = threading.Event()
        self._poll_thread = None
        self._gui_thread = None
        self._monitorables = set()
        self._items = []  # list of item descriptors
        self._build_layout()

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

    def _build_layout(self):
        # headers
        header = [
            sg.Text("name", size=(40, 1)),
            sg.Text("current", size=(30, 1)),
            sg.Text("control", size=(40, 1)),
        ]
        rows = [header, [sg.HorizontalSeparator()]]

        items = self._get_display_items()
        for item in items:
            name = _label_of(item, assembly=self.assembly)
            key_val = f"VAL::{name}"
            key_input = f"IN::{name}"
            key_step = f"STEP::{name}"
            key_up = f"UP::{name}"
            key_down = f"DOWN::{name}"
            key_set = f"SET::{name}"
            key_combo = f"COMBO::{name}"

            # initial current value (best-effort)
            try:
                cur = item.get_current_value()
            except Exception:
                cur = "<error>"

            # build control depending on type
            control_elems = []
            # Detector (non-Adjustable): read-only
            if isinstance(item, Detector) and not isinstance(item, Adjustable):
                control_elems = [sg.Text("read-only (Detector)")]
            # Adjustable
            elif isinstance(item, Adjustable):
                # enum
                if isinstance(item, AdjustableEnum):
                    opts = _get_enum_options(item) or []
                    # coerce to strings for display but keep values in values list
                    combo = sg.Combo(
                        values=[str(o) for o in opts],
                        default_value=(
                            str(cur)
                            if cur is not None
                            else (str(opts[0]) if opts else "")
                        ),
                        key=key_combo,
                        size=(20, 1),
                    )
                    control_elems = [combo, sg.Button("Set", key=key_set)]
                else:
                    # numeric?
                    if isinstance(cur, (int, float)) and not isinstance(cur, bool):
                        step_input = sg.Input(
                            default_text=str(1 if isinstance(cur, int) else 0.1),
                            size=(8, 1),
                            key=key_step,
                        )
                        up = sg.Button("▲", key=key_up)
                        down = sg.Button("▼", key=key_down)
                        val_in = sg.Input(
                            default_text=str(cur), size=(12, 1), key=key_input
                        )
                        set_btn = sg.Button("Set", key=key_set)
                        control_elems = [step_input, up, down, val_in, set_btn]
                    else:
                        # non-numeric adjustable: text input + set
                        val_in = sg.Input(
                            default_text=str(cur), size=(20, 1), key=key_input
                        )
                        set_btn = sg.Button("Set", key=key_set)
                        control_elems = [val_in, set_btn]
            # fallback: if has set_target_value, allow text entry
            elif hasattr(item, "set_target_value") and callable(
                getattr(item, "set_target_value")
            ):
                val_in = sg.Input(default_text=str(cur), size=(20, 1), key=key_input)
                set_btn = sg.Button("Set", key=key_set)
                control_elems = [val_in, set_btn]
            else:
                control_elems = [sg.Text("—")]

            # row: name label, current value label, control elements
            row = [
                sg.Text(name, size=(40, 1)),
                sg.Text(str(cur), size=(30, 1), key=key_val),
                sg.Column([control_elems], pad=(0, 0)),
            ]
            rows.append(row)

            # store descriptor
            self._items.append(
                {
                    "item": item,
                    "name": name,
                    "key_val": key_val,
                    "key_input": key_input,
                    "key_step": key_step,
                    "key_up": key_up,
                    "key_down": key_down,
                    "key_set": key_set,
                    "key_combo": key_combo,
                }
            )

            # register monitorable callbacks if provided
            if isinstance(item, MonitorableValueUpdate):
                self._monitorables.add(item)
                cb_setter = None
                for setter_name in (
                    "set_current_value_update",
                    "set_value_update_callback",
                    "on_value_update",
                ):
                    if hasattr(item, setter_name) and callable(
                        getattr(item, setter_name)
                    ):
                        cb_setter = getattr(item, setter_name)
                        break
                if cb_setter:

                    def make_cb(k):
                        def _cb(val):
                            try:
                                # push to GUI thread safely
                                if self.window is not None:
                                    self.window.write_event_value(("MB_UPDATE", k), val)
                            except Exception:
                                pass

                        return _cb

                    try:
                        cb_setter(make_cb(key_val))
                    except Exception:
                        # ignore registration failures
                        pass

        # Add a Close button row
        rows.append([sg.HorizontalSeparator()])
        rows.append([sg.Button("Close"), sg.Button("Refresh values")])

        self.layout = rows
        # create window
        self.window = sg.Window(
            f"Assembly Display - {getattr(self.assembly, 'name', '')}",
            self.layout,
            finalize=True,
        )

    def _poll_loop(self):
        # poll non-monitorable items and push updates into GUI via write_event_value
        while not self._stop_event.wait(self.poll_interval):
            for desc in self._items:
                it = desc["item"]
                if it in self._monitorables:
                    continue
                try:
                    val = it.get_current_value()
                    self.window.write_event_value(("MB_UPDATE", desc["key_val"]), val)
                except Exception:
                    # ignore single failures
                    pass

    def _handle_gui_event(self, event, values):
        try:
            if event == sg.WIN_CLOSED or event == "Close":
                self.stop()
                return False
            if event == "Refresh values":
                # force immediate refresh of all non-monitorables
                for desc in self._items:
                    it = desc["item"]
                    try:
                        val = it.get_current_value()
                        self.window.Element(desc["key_val"]).Update(str(val))
                    except Exception:
                        pass
                return True

            # monitorable update events pushed by write_event_value
            if isinstance(event, tuple) and event[0] == "MB_UPDATE":
                key = event[1]
                val = values.get(event) if event in values else values.get(event)
                # PySimpleGUI puts the pushed value in the "values" dict under the event tuple
                pushed = values.get(event, None)
                # for our usage, pushed contains the value; sometimes event tuple is used as key, sometimes not
                new_val = pushed if pushed is not None else val
                # find element and update
                try:
                    # event was MB_UPDATE, key is the element key we want to update
                    self.window.Element(key).Update(str(new_val))
                except Exception:
                    pass
                return True

            # control button handling: look for up/down/set/combo events
            # event keys are of form "UP::name", "DOWN::name", "SET::name", "COMBO::name"
            if isinstance(event, str):
                if (
                    event.startswith("UP::")
                    or event.startswith("DOWN::")
                    or event.startswith("SET::")
                    or event.startswith("COMBO::")
                ):
                    # find descriptor
                    name = event.split("::", 1)[1]
                    desc = next((d for d in self._items if d["name"] == name), None)
                    if desc is None:
                        return True
                    it = desc["item"]
                    key_val = desc["key_val"]
                    # handle combo set
                    if event.startswith("COMBO::"):
                        # a combo value changed; values[event] contains the selected string
                        sel = values.get(event)
                        # try map back to actual option values if AdjustableEnum provides raw options list
                        try:
                            opts = _get_enum_options(it) or []
                            # if option strings match raw, pick raw matching index
                            raw = None
                            for o in opts:
                                if str(o) == str(sel):
                                    raw = o
                                    break
                            if raw is None:
                                raw = sel
                            r = it.set_target_value(raw)
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                            # update displayed value
                            try:
                                newv = it.get_current_value()
                                self.window.Element(key_val).Update(str(newv))
                            except Exception:
                                pass
                        except Exception:
                            pass
                        return True

                    # handle SET
                    if event.startswith("SET::"):
                        # prefer numeric input key, else combo
                        input_key = desc.get("key_input")
                        combo_key = desc.get("key_combo")
                        sel_val = None
                        if combo_key and values.get(combo_key) is not None:
                            sel_val = values.get(combo_key)
                        elif input_key and values.get(input_key) is not None:
                            sel_val = values.get(input_key)
                        # coerce simple numeric if looks like number
                        try:
                            if isinstance(sel_val, str):
                                s = sel_val.strip()
                                if s.lower() in ("true", "false"):
                                    val = s.lower() == "true"
                                else:
                                    try:
                                        val = int(s)
                                    except Exception:
                                        try:
                                            val = float(s)
                                        except Exception:
                                            val = sel_val
                            else:
                                val = sel_val
                        except Exception:
                            val = sel_val
                        try:
                            r = it.set_target_value(val)
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                            # update display
                            try:
                                newv = it.get_current_value()
                                self.window.Element(key_val).Update(str(newv))
                            except Exception:
                                pass
                        except Exception:
                            pass
                        return True

                    # handle UP / DOWN for numeric adjustables
                    if event.startswith("UP::") or event.startswith("DOWN::"):
                        step_key = desc.get("key_step")
                        input_key = desc.get("key_input")
                        try:
                            step_raw = values.get(step_key)
                            step = (
                                float(step_raw) if step_raw not in (None, "") else 1.0
                            )
                        except Exception:
                            step = 1.0
                        # get base value from input if present else from current
                        try:
                            base_raw = values.get(input_key)
                            if base_raw is None or base_raw == "":
                                base = it.get_current_value()
                            else:
                                # coerce
                                try:
                                    base = int(base_raw)
                                except Exception:
                                    try:
                                        base = float(base_raw)
                                    except Exception:
                                        base = base_raw
                        except Exception:
                            base = 0
                        if event.startswith("UP::"):
                            try:
                                newv = base + step
                            except Exception:
                                newv = base
                        else:
                            try:
                                newv = base - step
                            except Exception:
                                newv = base
                        # set it
                        try:
                            r = it.set_target_value(newv)
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                            # update input and display
                            try:
                                if input_key:
                                    self.window.Element(input_key).Update(str(newv))
                                self.window.Element(key_val).Update(
                                    str(it.get_current_value())
                                )
                            except Exception:
                                pass
                        except Exception:
                            pass
                        return True

            return True
        except Exception:
            # swallow GUI handler exceptions to keep UI alive
            traceback.print_exc()
            return True

    def run(self):
        """Blocking run of the GUI event loop."""
        if self.window is None:
            self._build_layout()

        # start poll thread
        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        try:
            while True:
                event, values = self.window.read(timeout=100)
                cont = self._handle_gui_event(event, values)
                if cont is False:
                    break
        finally:
            self.stop()

    def _gui_thread_target(self):
        # wrapper for background thread run
        self.run()

    def start(self):
        """Start GUI in background thread (non-blocking)."""
        if self._gui_thread and self._gui_thread.is_alive():
            return
        self._stop_event.clear()
        self._gui_thread = threading.Thread(target=self._gui_thread_target, daemon=True)
        self._gui_thread.start()

    def stop(self):
        """Stop polling and close window."""
        self._stop_event.set()
        try:
            if self.window is not None:
                self.window.close()
                self.window = None
        except Exception:
            pass
