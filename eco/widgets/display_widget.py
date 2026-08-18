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

Row colouring (background per group, green/red per state) mirrors the
terminal `rich` table look (`eco.utilities.tables.section_row_styles`, also
used by `Assembly.get_display_str()`/`Beamline.diagram()`) so an assembly
looks the same whether you're in a terminal or a notebook -- see
`_section_css_classes`/`_state_css_class` below for how each is derived here.
"""
import enum
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

import ipywidgets as widgets
from IPython.display import display

from eco.utilities.tables import section_row_styles


def _format_value(value: Any) -> str:
    """Show enum-valued readbacks by their label (describer) rather than the
    underlying integer."""
    if isinstance(value, enum.Enum):
        return value.name
    return str(value)


def _enum_options(item: Any, cur: Any):
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


# ---- row colouring: background-per-group (rows) and green/red-per-state ---
# (value label) -- both driven purely by what's already on hand for a row
# (its dotted name, its item, its current value), no beamline-specific "kind"
# registry needed, so this works for any assembly, vacuum-related or not.
# ipywidgets' `Layout` has no portable `background`/`color` trait to set
# directly, so -- like the existing "hidden items" box a bit further down --
# colours are applied via `add_class` plus one consolidated injected <style>
# block, the proven-working pattern already used in this file.

_STATE_CSS = {True: "eco-state-green", False: "eco-state-red"}


def _rich_bg_to_css_hex(style):
    """Convert one of `section_row_styles()`'s rich style strings ("",
    "on grey30", "on #RRGGBB") to a CSS hex colour, or None for "no special
    background" (the plain "" case)."""
    if not style:
        return None
    if style.startswith("on #"):
        return style[3:]
    if style == "on grey30":
        return "#4d4d4d"  # rich ANSI grey30, approximated for CSS
    return None


def _section_css_classes(names):
    """Given the display names of a run of rows (in order), return
    (css_class_per_row, style_block) -- `css_class_per_row[i]` is the class to
    `add_class()` on row i (or None for "leave it plain"), and `style_block`
    is one `<style>...</style>` string defining every class used. Grouping is
    by the first dotted segment of each name (same convention as
    `Assembly.get_display_str()`), so a nested/"unfolded" sub-assembly's rows
    are set apart from unrelated single-line entries the same way there."""
    group_keys = [n.split(".", 1)[0] for n in names]
    row_styles = section_row_styles(group_keys)  # rich strings, one per row
    css_classes = []
    rules = {}
    for style in row_styles:
        hexcolor = _rich_bg_to_css_hex(style)
        if hexcolor is None:
            css_classes.append(None)
            continue
        cls = "eco-sec-" + hexcolor.lstrip("#")
        rules.setdefault(cls, f".{cls} {{ background: {hexcolor} !important; }}")
        css_classes.append(cls)
    style_block = "<style>" + "".join(rules.values()) + (
        f".{_STATE_CSS[True]} {{ color: #2e7d32; font-weight: 600; }}"
        f".{_STATE_CSS[False]} {{ color: #c62828; font-weight: 600; }}"
    ) + "</style>"
    return css_classes, style_block


def _state_css_class(item, value):
    """Best-effort green/red CSS class for a row's *value* label, from
    whatever's already available -- no beamline-specific metadata needed:

    - the displayed value is exactly "open"/"closed" (any Valve/shutter/
      stopper-shaped item using that convention -- e.g. eco.devices_general.
      vacuum.Valve.get_current_value()) -> green/red;
    - the item has a `get_severity()` (any `AdjustablePv`/`DetectorPvData`,
      e.g. a `VacuumGauge.pressure`) and its live EPICS alarm severity is
      NO_ALARM/MINOR-or-MAJOR -> green/red.

    None (no class, plain colour) for anything else, including a severity of
    INVALID or one that couldn't be read -- never guessed.
    """
    if isinstance(value, str):
        low = value.strip().lower()
        if low == "open":
            return _STATE_CSS[True]
        if low == "closed":
            return _STATE_CSS[False]
    get_severity = getattr(item, "get_severity", None)
    if callable(get_severity):
        try:
            severity = get_severity()
        except Exception:
            severity = None
        if severity == 0:
            return _STATE_CSS[True]
        if severity in (1, 2):
            return _STATE_CSS[False]
    return None


# Adaptive polling: an item whose get_current_value() is slow to respond gets
# polled less often, so slow/blocking readbacks don't starve the GUI and the
# fast items. An item's poll interval becomes ~ max(base, duration * FACTOR),
# capped at MAX_INTERVAL. Fast items stay at the base interval.
_POLL_SLOWDOWN_FACTOR = 10.0
_POLL_MAX_INTERVAL = 30.0

_PREFETCH_ERROR = object()  # sentinel: a prefetched read raised


# Responsive layout: columns use flexbox with a preferred basis + min-width and
# the rows are allowed to wrap, so on a wide screen everything sits on one line
# but on a narrow one (e.g. a phone via Voila) the name / value / control
# sections reflow onto separate lines instead of overflowing horizontally.
def _name_layout():
    return widgets.Layout(flex="1 1 160px", min_width="110px")


def _value_layout():
    return widgets.Layout(flex="1 1 120px", min_width="80px")


def _control_layout():
    return widgets.Layout(
        flex="2 1 260px", min_width="200px",
        flex_flow="row wrap", align_items="center",
    )


def _row_layout():
    return widgets.Layout(flex_flow="row wrap", align_items="center", width="100%")


def _prefetch_values(items, max_workers=8):
    """Read all initial values concurrently so building the widget for a large
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


def make_assembly_widget(
    assembly,
    poll_interval: float = 1.0,
    auto_start: bool = True,
    show_hidden: bool = False,
):
    """
    Build an ipywidgets VBox showing items in assembly.display_collection.
    For items that are eco.Adjustable a tweak control with up/down buttons and step size is shown.
    For items that are eco.Detector (and not Adjustable) no control is added.
    For other items, a readonly display is shown.
    Items that exist on the assembly but aren't part of its display collection
    are hidden behind an "expand hidden" toggle at the bottom (show_hidden=True
    unfolds them immediately).
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

    try:
        all_items = assembly.status_collection.get_list()
    except Exception:
        all_items = []
    hidden_items = [it for it in all_items if it not in display_items]

    # read every initial value concurrently up front, so opening a large
    # assembly is bounded by the slowest single readback, not their sum
    prefetched = _prefetch_values(list(display_items) + hidden_items)

    header = widgets.HBox(
        [
            widgets.HTML(value="<b>name</b>", layout=_name_layout()),
            widgets.HTML(value="<b>current</b>", layout=_value_layout()),
            widgets.HTML(value="<b>control</b>", layout=_control_layout()),
        ],
        layout=_row_layout(),
    )

    def _build_row(item, active=True, section_class=None):
        """Build the row(s) for a single display item. Returns a list of
        widgets to place in sequence (a row, optionally followed by a
        child-assembly detail box). Also registers polling/child-toggle
        state as a side effect. `active` controls whether the item is polled
        (hidden items start inactive until expanded). `section_class`, if
        given, is a CSS class (see `_section_css_classes`) applied to the row
        for its group background colour."""
        elements = []
        name = (
            item.alias.get_full_name(base=assembly)
            if hasattr(item, "alias")
            else getattr(item, "name", str(item))
        )

        if isinstance(item, AdjustableTrigger):
            # no value to show/poll -- see AdjustableTrigger's docstring
            name_w = widgets.Label(str(name), layout=_name_layout())
            value_w = widgets.Label("", layout=_value_layout())
            btn = widgets.Button(
                description=item.button_label or "▶ Trigger",
                layout=widgets.Layout(width="100px"),
                tooltip=item.doc or "",
            )

            def _on_trigger(b, it=item, button=btn):
                def _run():
                    try:
                        it.trigger()
                    except Exception:
                        old_color = button.style.button_color
                        button.style.button_color = "#ffb3b3"

                        def _reset(o=old_color):
                            time.sleep(1.2)
                            button.style.button_color = o

                        threading.Thread(target=_reset, daemon=True).start()

                threading.Thread(target=_run, daemon=True).start()

            btn.on_click(_on_trigger)
            control_box = widgets.HBox(layout=_control_layout())
            control_box.children = (btn,)
            row = widgets.HBox([name_w, value_w, control_box], layout=_row_layout())
            if section_class:
                row.add_class(section_class)
            elements.append(row)
            return elements  # not polled -- nothing to register in item_entries

        if id(item) in prefetched:
            cur = prefetched[id(item)]
            if cur is _PREFETCH_ERROR:
                cur = "<error>"
        else:
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
                layout=_name_layout(),
            )
            name_w.style.button_color = "#f5f5f5"
        else:
            name_w = widgets.Label(str(name), layout=_name_layout())
        value_w = widgets.Label(_format_value(cur), layout=_value_layout())
        state_class = _state_css_class(item, cur)
        if state_class:
            value_w.add_class(state_class)

        # control area
        control_box = widgets.HBox(layout=_control_layout())
        input_widget = None
        reader = None
        enum_opts = None
        suppress_dd = None

        # If it's a Detector and NOT Adjustable -> no control widget (readonly)
        if isinstance(item, Detector) and not isinstance(item, Adjustable):
            control_box.children = (widgets.Label("read-only (Detector)"),)

        # If it's Adjustable -> show tweak widget (step, up, down) or, for
        # enum-enabled adjustables, a dropdown of the enum options
        elif isinstance(item, Adjustable):
            original_value = cur
            enum_opts = _enum_options(item, cur)

            stop_btn = widgets.Button(
                description="🛑", layout=widgets.Layout(width="40px"),
                tooltip="Stop the current move",
            )
            reset_btn = widgets.Button(
                description="↺", layout=widgets.Layout(width="40px"),
                tooltip="Reset to the value from when this widget was opened",
            )
            last_changer = {"changer": None}

            if enum_opts is not None:
                # ENUM adjustable: dropdown selector, current readback preselected
                cur_label = _format_value(cur)
                dropdown = widgets.Dropdown(
                    options=enum_opts,
                    value=cur_label if cur_label in enum_opts else (enum_opts[0] if enum_opts else None),
                    layout=widgets.Layout(width="150px"),
                )
                input_widget = dropdown
                reader = None
                suppress_dd = [False]

                def _enum_set(label, b=None):
                    try:
                        r = item.set_target_value(label)
                        last_changer["changer"] = r
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
                            value_w.value = lbl
                            if lbl in enum_opts:
                                suppress_dd[0] = True
                                try:
                                    dropdown.value = lbl
                                finally:
                                    suppress_dd[0] = False
                    except Exception:
                        _flash_button_error(b)

                def _on_dd_change(change):
                    if suppress_dd[0] or change.get("name") != "value":
                        return
                    _enum_set(change["new"])

                def _on_enum_reset(b=None):
                    orig = original_value
                    _enum_set(orig.name if isinstance(orig, enum.Enum) else orig, b)

                def _on_enum_stop(b=None):
                    changer = last_changer.get("changer")
                    if changer is not None and hasattr(changer, "stop"):
                        try:
                            changer.stop()
                        except Exception:
                            _flash_button_error(b)

                dropdown.observe(_on_dd_change, names="value")
                stop_btn.on_click(_on_enum_stop)
                reset_btn.on_click(_on_enum_reset)
                control_box.children = (dropdown, stop_btn, reset_btn)

            else:
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
                            vw.value = _format_value(it.get_current_value())
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

        row = widgets.HBox([name_w, value_w, control_box], layout=_row_layout())
        if section_class:
            row.add_class(section_class)
        elements.append(row)
        item_entries.append(
            {
                "item": item,
                "value_widget": value_w,
                "input_widget": input_widget,
                "reader": reader,
                "active": active,
                "interval": poll_interval,
                "next_due": 0.0,  # monotonic time; 0 => poll on first pass
                "state_class": state_class,  # seeded from the initial build
                "enum_opts": enum_opts,
                "dd_suppress": suppress_dd,
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
            elements.append(child_detail_box)

        return elements

    # group-background classes for the visible rows, computed up front so all
    # of them share one consolidated <style> block (not one per row); hidden
    # items are left plain (their whole box already has its own background,
    # see below -- individually recolouring rows inside it would look muddy).
    display_names = [
        item.alias.get_full_name(base=assembly) if hasattr(item, "alias")
        else getattr(item, "name", str(item))
        for item in display_items
    ]
    section_classes, section_style_block = _section_css_classes(display_names)
    for item, section_class in zip(display_items, section_classes):
        rows.extend(_build_row(item, section_class=section_class))

    final_children = [widgets.HTML(value=section_style_block), header] + rows

    if hidden_items:
        hidden_entry_start = len(item_entries)
        hidden_rows = []
        for item in hidden_items:
            hidden_rows.extend(_build_row(item, active=show_hidden))
        hidden_entries = item_entries[hidden_entry_start:]

        hidden_box = widgets.VBox(hidden_rows)
        hidden_box.add_class("eco-hidden-items-box")
        hidden_style = widgets.HTML(
            value=(
                "<style>.eco-hidden-items-box "
                "{ background: #eaeaea; padding: 6px; border-radius: 4px; }</style>"
            )
        )
        hidden_container = widgets.VBox(
            [hidden_style, hidden_box], layout=widgets.Layout(margin="6px 0 0 0")
        )
        hidden_container.layout.display = None if show_hidden else "none"

        toggle_btn = widgets.Button(
            description="hide hidden" if show_hidden else "expand hidden",
            layout=widgets.Layout(width="150px"),
        )

        def _toggle_hidden(b, box=hidden_container, entries=hidden_entries):
            showing = box.layout.display != "none"
            if showing:
                box.layout.display = "none"
                b.description = "expand hidden"
                # stop polling collapsed items so they don't consume resources
                for ent in entries:
                    ent["active"] = False
            else:
                box.layout.display = None
                b.description = "hide hidden"
                for ent in entries:
                    ent["active"] = True
                    ent["next_due"] = 0.0  # refresh immediately on expand

        toggle_btn.on_click(_toggle_hidden)
        final_children.append(toggle_btn)
        final_children.append(hidden_container)

    memory_state = {"widget": None}
    if hasattr(assembly, "memory"):
        memory_box = widgets.VBox([], layout=widgets.Layout(margin="6px 0 0 0"))
        memory_btn = widgets.Button(
            description="▸ memories", layout=widgets.Layout(width="150px")
        )

        def _toggle_memory_browser(b, box=memory_box, state=memory_state):
            if state["widget"] is None:
                from eco.widgets.memory_widget import make_memory_browser_ipywidgets

                state["widget"] = make_memory_browser_ipywidgets(assembly)
                box.children = (state["widget"],)
                b.description = "▾ memories"
            else:
                box.children = ()
                state["widget"] = None
                b.description = "▸ memories"

        memory_btn.on_click(_toggle_memory_browser)
        final_children.append(memory_btn)
        final_children.append(memory_box)

    vbox = widgets.VBox(final_children)

    # background updater
    stop_event = threading.Event()
    updater_thread = None

    def _update_loop():
        # adaptive scheduler: poll each active item only when it is due, and
        # back off items whose readback is slow so they don't stall the rest
        while not stop_event.is_set():
            now = time.monotonic()
            next_wakeup = now + poll_interval
            for ent in item_entries:
                if stop_event.is_set():
                    break
                if not ent.get("active", True):
                    continue
                if now < ent["next_due"]:
                    next_wakeup = min(next_wakeup, ent["next_due"])
                    continue
                t0 = time.monotonic()
                try:
                    val = ent["item"].get_current_value()
                    text = _format_value(val)
                    ent["value_widget"].value = text
                    # keep the dropdown selection tracking the readback (e.g.
                    # a valve settling from a stale/mismatched selection to
                    # its true OPEN/CLOSED state); suppress_dd (the same flag
                    # _enum_set uses after a real user-driven set) stops this
                    # from looping back into _on_dd_change and issuing a
                    # spurious write.
                    enum_opts = ent.get("enum_opts")
                    dd_suppress = ent.get("dd_suppress")
                    if enum_opts and dd_suppress is not None and text in enum_opts:
                        dd = ent["input_widget"]
                        if dd.value != text:
                            dd_suppress[0] = True
                            try:
                                dd.value = text
                            finally:
                                dd_suppress[0] = False
                    # live green/red re-colouring -- open/closed and alarm
                    # severity can change between polls, so this must track
                    # the value, not just be set once at build time.
                    new_class = _state_css_class(ent["item"], val)
                    if new_class != ent.get("state_class"):
                        for cls in _STATE_CSS.values():
                            ent["value_widget"].remove_class(cls)
                        if new_class:
                            ent["value_widget"].add_class(new_class)
                        ent["state_class"] = new_class
                except Exception:
                    pass
                duration = time.monotonic() - t0
                ent["interval"] = min(
                    _POLL_MAX_INTERVAL,
                    max(poll_interval, duration * _POLL_SLOWDOWN_FACTOR),
                )
                ent["next_due"] = time.monotonic() + ent["interval"]
                next_wakeup = min(next_wakeup, ent["next_due"])
            sleep_for = max(0.05, next_wakeup - time.monotonic())
            if stop_event.wait(sleep_for):
                break

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
