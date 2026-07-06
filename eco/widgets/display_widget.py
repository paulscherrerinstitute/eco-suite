# ...existing code...
"""
Jupyter widget to view display items and (where supported) set targets.

Usage:
    from eco.widgets.display_widget import make_assembly_widget
    w = make_assembly_widget(my_assembly, poll_interval=1.0)
    display(w)

Returned widget has methods:
    w.start()  # start background polling (already started by default)
    w.stop()   # stop background polling
"""
import threading
import time
from typing import Any, List

import ipywidgets as widgets
from IPython.display import display

# Try to import types for isinstance checks if available.
try:
    from eco import Adjustable, Detector
    from eco.elements.assembly import Assembly
except Exception:
    Adjustable = object
    Detector = object
    Assembly = object


def _make_input_widget_for_value(value: Any):
    """Return a suitable ipywidget for editing a value, plus a function to read it."""
    if isinstance(value, bool):
        w = widgets.Checkbox(value=value)
        reader = lambda: w.value
    elif isinstance(value, (int,)) and not isinstance(value, bool):
        w = widgets.IntText(value=value)
        reader = lambda: int(w.value)
    elif isinstance(value, (float,)):
        w = widgets.FloatText(value=value)
        reader = lambda: float(w.value)
    else:
        # fallback to text field (strings, enums represented as strings)
        w = widgets.Text(value=str(value) if value is not None else "")
        reader = lambda: w.value
    return w, reader


def _make_step_widget_for_value(value: Any):
    """Create step-size input suitable for numeric types."""
    if isinstance(value, int) and not isinstance(value, bool):
        step_w = widgets.IntText(value=1, layout=widgets.Layout(width="80px"))
        reader = lambda: int(step_w.value)
    else:
        step_w = widgets.FloatText(
            value=0.1 if isinstance(value, float) else 1.0,
            layout=widgets.Layout(width="80px"),
        )
        reader = lambda: float(step_w.value)
    return step_w, reader


def _is_tweakable(value: Any) -> bool:
    """Only plain numeric values support +/- step tweaking; e.g. strings don't."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _flash_button_error(btn):
    if btn is None:
        return
    old = btn.description
    btn.description = "Err"

    def _reset(b=btn, o=old):
        time.sleep(1.2)
        b.description = o

    threading.Thread(target=_reset, daemon=True).start()


def make_assembly_widget(assembly, poll_interval: float = 1.0, auto_start: bool = True):
    """
    Build an ipywidgets VBox showing items in assembly.display_collection.
    For items that are eco.Adjustable a tweak control with up/down buttons and step size is shown.
    For items that are eco.Detector (and not Adjustable) no control is added.
    For other items, a readonly display is shown.
    Returns a VBox widget; the returned widget has .start() and .stop() methods
    to control the background polling thread.
    """
    rows: List[widgets.HBox] = []
    item_entries = []  # list of dicts with item -> widgets and reader
    child_states = []  # state dicts of any expanded child-assembly widgets

    # obtain list of display items (support either call or attribute)
    try:
        display_items = assembly.display_collection()
    except Exception:
        try:
            display_items = assembly.status_collection.get_list(selection="display")
        except Exception:
            display_items = []

    header = widgets.HBox(
        [
            widgets.HTML(value="<b>name</b>", layout=widgets.Layout(width="30%")),
            widgets.HTML(value="<b>current</b>", layout=widgets.Layout(width="40%")),
            widgets.HTML(value="<b>control</b>", layout=widgets.Layout(width="30%")),
        ]
    )

    for item in display_items:
        name = (
            item.alias.get_full_name(base=assembly)
            if hasattr(item, "alias")
            else getattr(item, "name", str(item))
        )
        try:
            cur = item.get_current_value()
        except Exception:
            cur = "<error>"

        # child assemblies get a clickable name that expands/collapses their
        # own widget directly below this row; everything else is a plain label
        is_child_assembly = isinstance(item, Assembly)
        child_detail_box = widgets.VBox(
            [], layout=widgets.Layout(margin="0 0 0 24px")
        )
        child_state = {"widget": None}
        if is_child_assembly:
            name_w = widgets.Button(
                description="▸ " + str(name),
                layout=widgets.Layout(width="30%"),
            )
            name_w.style.button_color = "#f5f5f5"
        else:
            name_w = widgets.Label(str(name), layout=widgets.Layout(width="30%"))
        value_w = widgets.Label(str(cur), layout=widgets.Layout(width="40%"))

        # control area
        control_box = widgets.HBox(layout=widgets.Layout(width="30%"))
        input_widget = None
        reader = None

        # If it's a Detector and NOT Adjustable -> no control widget (readonly)
        if isinstance(item, Detector) and not isinstance(item, Adjustable):
            control_box.children = (widgets.Label("read-only (Detector)"),)

        # If it's Adjustable -> show tweak widget (step, up, down)
        elif isinstance(item, Adjustable):
            original_value = cur
            tweakable = _is_tweakable(cur)

            # optional direct input to set an absolute value
            if not isinstance(cur, (list, dict)) and not isinstance(
                cur, (bytes, bytearray)
            ):
                input_widget, reader = _make_input_widget_for_value(cur)
                input_widget.layout.margin = "0 6px 0 0"
            else:
                input_widget = widgets.Label("n/a", layout=widgets.Layout(width="80px"))
                reader = None

            stop_btn = widgets.Button(
                description="🛑", layout=widgets.Layout(width="36px"),
                tooltip="Stop the current move",
            )
            reset_btn = widgets.Button(
                description="↺", layout=widgets.Layout(width="36px"),
                tooltip="Reset to the value from when this widget was opened",
            )
            last_changer = {"changer": None}

            def make_handlers(it, val_widget, inp_widget, inp_reader, changer_ref):
                # guards recursive triggering of the input's on-change handler
                # when we update inp_widget.value ourselves after a move
                suppress_input_event = [False]

                def _sync_input_widget(value):
                    if inp_reader is None:
                        return
                    suppress_input_event[0] = True
                    try:
                        inp_widget.value = value
                    except Exception:
                        pass
                    finally:
                        suppress_input_event[0] = False

                def _do_set(newval, btn=None):
                    try:
                        r = it.set_target_value(newval)
                        changer_ref["changer"] = r
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            new_current = it.get_current_value()
                        except Exception:
                            new_current = newval
                        try:
                            val_widget.value = str(new_current)
                        except Exception:
                            pass
                        # always reflect the real current value, so the next
                        # tweak/move starts from where the device actually is
                        _sync_input_widget(new_current)
                    except Exception:
                        _flash_button_error(btn)

                def _on_input_change(change):
                    if suppress_input_event[0] or inp_reader is None:
                        return
                    if change.get("name") != "value":
                        return
                    _do_set(inp_reader(), None)

                def _on_stop(b=None):
                    changer = changer_ref.get("changer")
                    if changer is not None and hasattr(changer, "stop"):
                        try:
                            changer.stop()
                        except Exception:
                            _flash_button_error(b)

                def _on_reset(b=None):
                    _do_set(original_value, b)

                return _do_set, _on_input_change, _on_stop, _on_reset

            do_set, on_input_change, on_stop, on_reset = make_handlers(
                item, value_w, input_widget, reader, last_changer
            )
            # set the value as soon as a new one is entered (on Enter/blur,
            # not per keystroke) instead of requiring a separate "Set" button
            if reader is not None:
                if hasattr(input_widget, "continuous_update"):
                    input_widget.continuous_update = False
                input_widget.observe(on_input_change, names="value")
            stop_btn.on_click(on_stop)
            reset_btn.on_click(on_reset)

            control_children = []
            if tweakable:
                # only plain numbers support +/- step tweaking (e.g. not strings)
                step_w, step_reader = _make_step_widget_for_value(cur)
                up_btn = widgets.Button(
                    description="▲", layout=widgets.Layout(width="40px")
                )
                down_btn = widgets.Button(
                    description="▼", layout=widgets.Layout(width="40px")
                )

                def make_tweak(sign, it=item, sr=step_reader, ds=do_set):
                    def _on_click(b=None):
                        try:
                            step = sr()
                            base = it.get_current_value()
                            newval = base + sign * step
                        except Exception:
                            _flash_button_error(b)
                            return
                        ds(newval, b)

                    return _on_click

                up_btn.on_click(make_tweak(1))
                down_btn.on_click(make_tweak(-1))
                control_children.extend([step_w, up_btn, down_btn])

            control_children.append(input_widget)
            control_children.extend([stop_btn, reset_btn])
            control_box.children = tuple(control_children)

        # Fallback: if item has set_target_value (callable) but wasn't captured above, allow simple set
        elif hasattr(item, "set_target_value") and callable(
            getattr(item, "set_target_value")
        ):
            # create input widget based on current value
            input_widget, reader = _make_input_widget_for_value(cur)
            input_widget.layout.margin = "0 6px 0 0"

            def make_on_set(it, rw, vw, inp):
                def _on_set(change):
                    if change.get("name") != "value":
                        return
                    try:
                        val = rw()
                        r = it.set_target_value(val)
                        try:
                            if hasattr(r, "wait"):
                                r.wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            vw.value = str(it.get_current_value())
                        except Exception:
                            pass
                    except Exception:
                        old_border = inp.layout.border
                        inp.layout.border = "1px solid red"

                        def _reset(o=old_border):
                            time.sleep(1.2)
                            inp.layout.border = o

                        threading.Thread(target=_reset, daemon=True).start()

                return _on_set

            # set the value as soon as a new one is entered (on Enter/blur,
            # not per keystroke) instead of requiring a separate "Set" button
            if hasattr(input_widget, "continuous_update"):
                input_widget.continuous_update = False
            input_widget.observe(
                make_on_set(item, reader, value_w, input_widget), names="value"
            )
            control_box.children = (input_widget,)

        else:
            control_box.children = (
                widgets.Label("—", layout=widgets.Layout(margin="0 0 0 6px")),
            )

        row = widgets.HBox([name_w, value_w, control_box])
        rows.append(row)
        item_entries.append(
            {
                "item": item,
                "value_widget": value_w,
                "input_widget": input_widget,
                "reader": reader,
            }
        )

        if is_child_assembly:

            def make_toggle_handler(it, box, state, base_name):
                def _toggle(btn):
                    if state["widget"] is None:
                        child_widget = make_assembly_widget(
                            it, poll_interval=poll_interval
                        )
                        state["widget"] = child_widget
                        box.children = (child_widget,)
                        btn.description = "▾ " + base_name
                    else:
                        try:
                            state["widget"].stop()
                        except Exception:
                            pass
                        state["widget"] = None
                        box.children = ()
                        btn.description = "▸ " + base_name

                return _toggle

            name_w.on_click(make_toggle_handler(item, child_detail_box, child_state, str(name)))
            child_states.append(child_state)
            rows.append(child_detail_box)

    vbox = widgets.VBox([header] + rows)

    # background updater
    stop_event = threading.Event()
    updater_thread = None

    def _update_loop():
        while not stop_event.wait(poll_interval):
            for ent in item_entries:
                it = ent["item"]
                vw = ent["value_widget"]
                try:
                    val = it.get_current_value()
                    vw.value = str(val)
                except Exception:
                    pass

    def start():
        nonlocal updater_thread
        if updater_thread and updater_thread.is_alive():
            return
        stop_event.clear()
        updater_thread = threading.Thread(target=_update_loop, daemon=True)
        updater_thread.start()

    def stop():
        stop_event.set()
        for state in child_states:
            child_widget = state.get("widget")
            if child_widget is not None:
                try:
                    child_widget.stop()
                except Exception:
                    pass

    vbox.start = start
    vbox.stop = stop
    vbox._stop_event = stop_event

    if auto_start:
        start()

    return vbox


# ...existing code...
