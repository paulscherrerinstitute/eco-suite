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

Same responsiveness rules as the Qt grid (see eco.widgets.display_qt's module
docstring and eco.widgets.live_values, which both share): a click never
blocks the kernel -- before, it ran ``set_target_value`` + ``wait(timeout=5)``
in the widget callback, so the kernel handled no other widget message
(including that row's Stop) until the move ended; writes now run in a
background thread and a status dot per row shows connecting / moving /
monitored / polled / error (hover for the reason). Monitorable items are
monitored, the rest polled in parallel; opening waits at most
PREFETCH_DEADLINE for initial values; the tweak step of a motor record starts
from the IOC's TWV (edits write it back). Widget values may be set from any
thread (the change goes out as a comm message), which is what lets updates
reach the browser -- also in Voila -- while a cell is running.
"""
import enum
import html
import threading
import time
from typing import Any, List

import ipywidgets as widgets
from IPython.display import display

from eco.utilities.tables import section_row_styles
from eco.widgets import live_values
from eco.widgets.live_values import (  # the toolkit-independent part, shared with display_qt/display_tk
    LiveReader,
    PENDING as _PENDING,
    PREFETCH_ERROR as _PREFETCH_ERROR,
    initial_step as _initial_step,
)


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


def _action_group(stop_btn, reset_btn):
    """Wrap Stop/Reset in their own HBox with a CSS auto left-margin, so
    they sit flush against the right edge of `control_box` regardless of
    how many/wide the preceding controls are (dropdown vs. step/up/down/
    input vs. anything else) -- margin-left: auto on a flex child pushes it
    to the far end of its flex line, same trick as the Qt side's
    row.addStretch(1) placed just before these two buttons."""
    return widgets.HBox([stop_btn, reset_btn], layout=widgets.Layout(margin="0 0 0 auto"))


def _item_menu(item, name):
    """The "⋯" menu closing a row and the box below the row where what it
    opens appears (eco.widgets.item_menu_ipy) -- both row children; an
    empty list if the menu can't be built, so the row still works."""
    try:
        from eco.widgets import item_menu_ipy

        panels = item_menu_ipy.ItemPanels()
        return [item_menu_ipy.make_menu(item, name, panels), panels]
    except Exception:
        return []


def _stop_item_panels(row):
    for child in row.children[3:]:
        stop = getattr(child, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass


def _prefetch_values(items, max_workers=8, deadline=None):
    """See eco.widgets.live_values.prefetch_values."""
    return live_values.prefetch_values(items, max_workers=max_workers, deadline=deadline)

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


def _make_step_widget_for_value(value: Any, step=None):
    """Create step-size input suitable for numeric types, starting at `step`
    (default: 1 for ints, 0.1 for floats)."""
    if isinstance(value, int) and not isinstance(value, bool) and (step is None or float(step).is_integer()):
        step_w = widgets.IntText(value=int(step or 1), layout=widgets.Layout(width="80px"))
        reader = lambda: int(step_w.value)
    else:
        if step is None:
            step = 0.1 if isinstance(value, float) else 1.0
        step_w = widgets.FloatText(value=float(step), layout=widgets.Layout(width="80px"))
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


def _flash_widget_error(widget):
    """A failed write: a button shows "Err", anything else a red border,
    for 1.2 s."""
    if widget is None:
        return
    if isinstance(widget, widgets.Button):
        _flash_button_error(widget)
        return
    try:
        old = widget.layout.border
        widget.layout.border = "1px solid red"

        def _reset(o=old):
            time.sleep(1.2)
            widget.layout.border = o

        threading.Thread(target=_reset, daemon=True).start()
    except Exception:
        pass


class _RowState:
    """ipywidgets counterpart of display_qt._RowController: writes go
    through eco.widgets.live_values in background threads, and an HTML
    status dot shows what the row is doing (hover for the reason). Widget
    values may be set from any thread, so results are applied directly.
    ``on_finished`` (set by the grid) runs after every write -- it requests
    a re-read."""

    def __init__(self, item):
        self.item = item
        self.holder = {}  # "changer" of the latest write
        self.dot = widgets.HTML(layout=widgets.Layout(width="18px", min_width="18px"))
        self.on_finished = None
        self._read_state = ("waiting", "")
        self._writing = 0
        self._writing_lock = threading.Lock()
        self.show("waiting", "")

    def show(self, state, message):
        color, tooltip = live_values.dot_state(state, message, self._read_state)
        self.dot.value = (
            f'<span title="{html.escape(tooltip, quote=True)}" '
            f'style="color:{color}; font-size:16px; cursor:default">●</span>'
        )

    def read_ok(self, monitor_state):
        """monitor_state: LiveReader.monitor_state() ("live"/"subscribed"/None)."""
        self._read_state = (monitor_state or "polled", "")
        if not self._writing:
            self.show(*self._read_state)

    def read_failed(self, message):
        self._read_state = ("error", message)
        if not self._writing:
            self.show(*self._read_state)

    def write(self, target_fn, flash=None):
        with self._writing_lock:
            self._writing += 1
        self.show("busy", "")

        def on_state(state, message):
            if state in ("idle", "error"):
                with self._writing_lock:
                    self._writing -= 1
            self.show(state, message)
            if state == "error":
                _flash_widget_error(flash)

        def on_finished():
            if self.on_finished is not None:
                self.on_finished()

        live_values.start_write(self.item, target_fn, on_state, on_finished=on_finished, holder=self.holder)

    def stop(self):
        live_values.stop_move(self.item, self.holder.get("changer"), self.show)

    def attach_ioc_step(self, step_w):
        """Fill `step_w` from the IOC's TWV and write edits back to it."""
        suppress = [False]
        step_w.description_tooltip = "tweak step, from the IOC (TWV) -- editing it sets TWV"

        def on_step(value):
            suppress[0] = True
            try:
                step_w.value = type(step_w.value)(value)
            except Exception:
                pass
            finally:
                suppress[0] = False

        holder = live_values.load_ioc_step(self.item, on_step)

        def on_change(change):
            if not suppress[0]:
                live_values.store_ioc_step(holder, change["new"])

        step_w.observe(on_change, names="value")


def _build_item_controls_widget(item, value_w, cur, row_state=None):
    """Build the control area for one item -- the exact branch logic
    make_assembly_widget's _build_row applies per item in the assembly
    grid (Detector-read-only / Adjustable-enum / Adjustable-tweakable-or-
    plain / fallback-with-set_target_value / bare fallback), extracted so
    it's independently reusable (see
    eco.widgets.containers.adjustable_control/detector_indicator) as well
    as from the grid itself -- not a second implementation. Qt equivalent:
    eco.widgets.display_qt._build_item_controls_qt.

    Every write goes through `row_state` (a _RowState; one is made if not
    given), i.e. off the kernel's message thread; the new value shows
    through the caller's reads (_apply_result), which the row state asks
    for when a write finishes. Its status dot is the first widget of the
    control box.

    Returns (control_box: widgets.HBox, poll_state: dict) -- `poll_state`
    bundles whatever _apply_result needs beyond item/value_widget
    (input_widget/enum_opts/dd_suppress, for keeping an enum dropdown in
    sync with polled readings, and the row_state); merge it into an
    item_entries-shaped dict alongside "item"/"value_widget"."""
    if row_state is None:
        row_state = _RowState(item)
    control_box = widgets.HBox(layout=_control_layout())
    input_widget = None
    reader = None
    enum_opts = None
    suppress_dd = None
    children = [row_state.dot]

    # If it's a Detector and NOT Adjustable -> no control widget (readonly)
    if isinstance(item, Detector) and not isinstance(item, Adjustable):
        children.append(widgets.Label("read-only (Detector)"))

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
        stop_btn.on_click(lambda b: row_state.stop())

        if enum_opts is not None:
            # ENUM adjustable: dropdown selector, current readback preselected
            cur_label = _format_value(cur)
            dropdown = widgets.Dropdown(
                options=enum_opts,
                value=cur_label if cur_label in enum_opts else (enum_opts[0] if enum_opts else None),
                layout=widgets.Layout(width="150px"),
            )
            input_widget = dropdown
            suppress_dd = [False]

            def _on_dd_change(change):
                if suppress_dd[0] or change.get("name") != "value":
                    return
                label = change["new"]
                row_state.write(lambda: label, flash=dropdown)

            def _on_enum_reset(b=None):
                orig = original_value
                target = orig.name if isinstance(orig, enum.Enum) else orig
                row_state.write(lambda: target, flash=b)

            dropdown.observe(_on_dd_change, names="value")
            reset_btn.on_click(_on_enum_reset)
            children += [dropdown, _action_group(stop_btn, reset_btn)]

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

            # set the value as soon as a new one is entered (on Enter/blur,
            # not per keystroke) instead of requiring a separate "Set" button
            if reader is not None:
                if hasattr(input_widget, "continuous_update"):
                    input_widget.continuous_update = False

                def _on_input_change(change, rd=reader, inp=input_widget):
                    if change.get("name") != "value":
                        return
                    try:
                        target = rd()
                    except Exception:
                        _flash_widget_error(inp)
                        return
                    row_state.write(lambda: target, flash=inp)

                input_widget.observe(_on_input_change, names="value")

            reset_btn.on_click(lambda b, ref=original_value: row_state.write(lambda: ref, flash=b))

            if tweakable:
                # only plain numbers support +/- step tweaking (e.g. not strings)
                step_w, step_reader = _make_step_widget_for_value(cur, _initial_step(item, cur))
                if live_values.has_ioc_step(item):
                    row_state.attach_ioc_step(step_w)
                up_btn = widgets.Button(
                    description="▲", layout=widgets.Layout(width="40px")
                )
                down_btn = widgets.Button(
                    description="▼", layout=widgets.Layout(width="40px")
                )

                def make_tweak(sign, it=item, sr=step_reader):
                    def _on_click(b=None):
                        try:
                            step = sr()
                        except Exception:
                            _flash_button_error(b)
                            return
                        # relative to the real current value, read in the background
                        row_state.write(lambda: it.get_current_value() + sign * step, flash=b)

                    return _on_click

                up_btn.on_click(make_tweak(1))
                down_btn.on_click(make_tweak(-1))
                # the setpoint comes first, right next to the readback; the
                # tweak controls follow it
                children += [input_widget, step_w, up_btn, down_btn]
            else:
                children.append(input_widget)

            children.append(_action_group(stop_btn, reset_btn))

    # Fallback: if item has set_target_value (callable) but wasn't captured above, allow simple set
    elif hasattr(item, "set_target_value") and callable(
        getattr(item, "set_target_value")
    ):
        # create input widget based on current value
        input_widget, reader = _make_input_widget_for_value(cur)
        input_widget.layout.margin = "0 6px 0 0"
        if hasattr(input_widget, "continuous_update"):
            input_widget.continuous_update = False

        def _on_set(change, rd=reader, inp=input_widget):
            if change.get("name") != "value":
                return
            try:
                target = rd()
            except Exception:
                _flash_widget_error(inp)
                return
            row_state.write(lambda: target, flash=inp)

        input_widget.observe(_on_set, names="value")
        children.append(input_widget)

    else:
        children.append(widgets.Label("—", layout=widgets.Layout(margin="0 0 0 6px")))

    control_box.children = tuple(children)
    poll_state = {
        "input_widget": input_widget,
        "reader": reader,
        "enum_opts": enum_opts,
        "dd_suppress": suppress_dd,
        "row_state": row_state,
    }
    return control_box, poll_state


def _apply_result(entry, ok, payload, monitor_state=None):
    """Show one read of `entry["item"]` -- `payload` is the value, or (not
    ok) why there is none: value label, enum-dropdown sync, state-class
    re-colouring and the row's status dot. Called from reader threads
    (widget values may be set from any thread)."""
    row_state = entry.get("row_state")
    if not ok:
        if row_state is not None:
            row_state.read_failed(payload)
        return
    val = payload
    text = _format_value(val)
    entry["value_widget"].value = text
    # keep the dropdown selection tracking the readback (e.g. a valve
    # settling from a stale/mismatched selection to its true OPEN/CLOSED
    # state); dd_suppress stops this from looping back into the dropdown's
    # change handler and issuing a spurious write.
    enum_opts = entry.get("enum_opts")
    dd_suppress = entry.get("dd_suppress")
    if enum_opts and dd_suppress is not None and text in enum_opts:
        dd = entry["input_widget"]
        if dd.value != text:
            dd_suppress[0] = True
            try:
                dd.value = text
            finally:
                dd_suppress[0] = False
    # live green/red re-colouring -- open/closed and alarm severity can
    # change between reads, so this must track the value, not just be set
    # once at build time.
    new_class = _state_css_class(entry["item"], val)
    if new_class != entry.get("state_class"):
        for cls in _STATE_CSS.values():
            entry["value_widget"].remove_class(cls)
        if new_class:
            entry["value_widget"].add_class(new_class)
        entry["state_class"] = new_class
    if row_state is not None:
        row_state.read_ok(monitor_state)


def _apply_item_update(entry):
    """Read `entry["item"]`'s current value here and show it (see
    _apply_result). Returns the elapsed read duration in seconds."""
    t0 = time.monotonic()
    try:
        value = entry["item"].get_current_value()
        if value is None:
            _apply_result(entry, False, "no value (not connected?)")
        else:
            _apply_result(entry, True, value)
    except Exception as exc:
        try:
            _apply_result(entry, False, live_values.describe(exc))
        except Exception:
            pass
    return time.monotonic() - t0


def build_adjustable_control_widget(item, poll_interval=1.0, title=None):
    """Standalone, self-updating version of one make_assembly_widget row
    (name/value/controls) -- for use as a eco.widgets.containers.stack()
    child outside the normal assembly grid, e.g. via
    eco.widgets.containers.adjustable_control()/detector_indicator() (the
    same function serves both -- _build_item_controls_widget already
    renders read-only for a plain, non-Adjustable Detector). Monitored where
    the item supports it, polled otherwise, writes off the kernel thread --
    same rules as the grid. Returns a plain ipywidgets.HBox with its own
    `.stop()` attached directly -- no wrapper object needed, since
    eco.widgets.widget_tray.teardown_widget already calls `.stop()` if
    present before closing a widget's subtree."""
    name = title or (
        item.alias.get_full_name()
        if hasattr(item, "alias")
        else getattr(item, "name", str(item))
    )
    try:
        cur = item.get_current_value()
    except Exception:
        cur = "<error>"

    name_w = widgets.Label(str(name), layout=_name_layout())
    value_w = widgets.Label(_format_value(cur), layout=_value_layout())
    state_class = _state_css_class(item, cur)
    if state_class:
        value_w.add_class(state_class)

    control_box, poll_state = _build_item_controls_widget(item, value_w, cur)
    row = widgets.HBox([name_w, value_w, control_box] + _item_menu(item, str(name)), layout=_row_layout())

    entry = {"item": item, "value_widget": value_w, "state_class": state_class, **poll_state}
    reader = LiveReader(
        lambda index, ok, payload: _apply_result(entry, ok, payload, reader.monitor_state(0)),
        poll_interval=poll_interval,
        workers=1,
    )
    reader.add(item)
    poll_state["row_state"].on_finished = lambda: reader.request_read(0)
    reader.start()

    def stop():
        reader.stop()
        _stop_item_panels(row)

    row.stop = stop
    return row


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
    # assembly is bounded by the slowest single readback, not their sum --
    # and by PREFETCH_DEADLINE at most (a late row shows "connecting...")
    prefetched = _prefetch_values(
        list(display_items) + hidden_items, deadline=live_values.PREFETCH_DEADLINE
    )

    # results of the reads arrive here, from reader threads (see _apply_result)
    def _on_result(index, ok, payload):
        entry = item_entries[index]
        if ok and entry.get("needs_controls"):
            _build_late_controls(entry, payload)
        _apply_result(entry, ok, payload, reader.monitor_state(index))

    def _build_late_controls(entry, value):
        """The first value of a row that had none at opening arrived: now its
        controls (enum? numeric?) can be built."""
        entry["needs_controls"] = False
        control_box, poll_state = _build_item_controls_widget(
            entry["item"], entry["value_widget"], value, entry["row_state"]
        )
        entry.update(poll_state)
        children = entry["row"].children
        entry["row"].children = (children[0], entry["value_widget"], control_box) + children[3:]

    reader = LiveReader(_on_result, poll_interval=poll_interval)

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

        pending = cur is _PENDING

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
        value_w = widgets.Label("…" if pending else _format_value(cur), layout=_value_layout())
        state_class = None if pending else _state_css_class(item, cur)
        if state_class:
            value_w.add_class(state_class)

        row_state = _RowState(item)
        if pending:
            # no value yet, so no way to tell which controls fit (enum?
            # numeric?): built when the first value arrives (_build_late_controls)
            control_box = widgets.HBox(
                [row_state.dot, widgets.Label("connecting…")], layout=_control_layout()
            )
            poll_state = {"row_state": row_state}
        else:
            control_box, poll_state = _build_item_controls_widget(item, value_w, cur, row_state)

        full_name = item.alias.get_full_name() if hasattr(item, "alias") else str(name)
        row = widgets.HBox(
            [name_w, value_w, control_box] + _item_menu(item, full_name), layout=_row_layout()
        )
        if section_class:
            row.add_class(section_class)
        elements.append(row)
        index = reader.add(item, active=active)  # same index as in item_entries
        row_state.on_finished = lambda i=index: reader.request_read(i)
        item_entries.append(
            {
                "item": item,
                "value_widget": value_w,
                "row": row,
                "needs_controls": pending,
                "state_class": state_class,  # seeded from the initial build
                **poll_state,
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

        def _toggle_hidden(b, box=hidden_container, start=hidden_entry_start, count=len(hidden_entries)):
            showing = box.layout.display != "none"
            box.layout.display = "none" if showing else None
            b.description = "expand hidden" if showing else "hide hidden"
            # only read/monitor expanded items, so collapsed ones don't consume resources
            for index in range(start, start + count):
                reader.set_active(index, not showing)

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

    # reads and monitors run off the kernel thread (see live_values.LiveReader)
    def start():
        reader.start()

    def stop():
        reader.stop()
        for entry in item_entries:
            _stop_item_panels(entry["row"])
        for state in child_states:
            child_widget = state.get("widget")
            if child_widget is not None:
                try:
                    child_widget.stop()
                except Exception:
                    pass

    vbox.start = start
    vbox.stop = stop
    vbox._reader = reader

    if auto_start:
        start()

    return vbox


# ...existing code...
