"""What the web client is told about eco objects and values -- plain,
JSON-serializable data, no tornado and no GUI imports (so it is unit-tested
on its own, see tests/test_web_viewmodels.py).

Mirrors what the property grids show (eco.widgets.display_widget /
display_qt): the rows are an assembly's display collection, everything else
in its status collection is "hidden"; the value text comes from
live_values.format_value. Kept as a small parallel implementation on purpose
rather than importing display_widget (ipywidgets) -- see web_frontend/
ARCHITECTURE.md on keeping the front-ends independent.
"""
import enum
import math
import numbers

from eco.web.camera import camera_info
from eco.widgets.live_values import format_value

MAX_TEXT = 300  # characters of a value's text sent to the client (arrays, long strings)


# -- values ----------------------------------------------------------------------------


def encode_value(value):
    """One readback as sent to the client: always a "text"; "num" for a
    finite real number (what the ▲/▼ buttons step from), "bool" for a bool,
    "enum" (the labels) for an enum, "cls" "green"/"red" for open/closed."""
    out = {"text": _clip(format_value(value))}
    if isinstance(value, enum.Enum):
        out["enum"] = [member.name for member in type(value)]
    elif isinstance(value, bool) or getattr(getattr(value, "dtype", None), "kind", None) == "b":
        out["bool"] = bool(value)
    elif isinstance(value, numbers.Real):
        try:
            number = float(value)
        except Exception:
            number = None
        if number is not None and math.isfinite(number):
            out["num"] = int(value) if isinstance(value, numbers.Integral) else number
    cls = state_class(value)
    if cls:
        out["cls"] = cls
    return out


def state_class(value):
    """"green"/"red" for the open/closed convention the grids colour
    (eco.widgets.display_widget._state_css_class), else None. The grids'
    second rule -- EPICS alarm severity -- needs an extra CA read per
    update and is left out here for now."""
    if isinstance(value, enum.Enum):
        value = value.name
    if isinstance(value, str):
        low = value.strip().lower()
        if low == "open":
            return "green"
        if low == "closed":
            return "red"
    return None


def coerce_target(text_or_value, last_value):
    """A target sent by the client, converted to the type of the item's
    last readback (the grids do the same through their typed input
    widgets): an enum takes its label, numbers are parsed, bools accept
    true/false/1/0."""
    value = text_or_value
    if isinstance(last_value, enum.Enum):
        return str(value)
    if isinstance(last_value, bool):
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"not a boolean: {value!r}")
        return bool(value)
    if isinstance(last_value, numbers.Integral):
        number = float(value)
        if not number.is_integer():
            raise ValueError(f"not an integer: {value!r}")
        return int(number)
    if isinstance(last_value, numbers.Real):
        return float(value)
    return value


def _clip(text):
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


# -- objects ---------------------------------------------------------------------------


def kind_of(obj):
    """"trigger" | "assembly" | "adjustable" | "detector" | "other"."""
    from eco.elements.adjustable import AdjustableTrigger
    from eco.elements.assembly import Assembly
    from eco.elements.protocols import Adjustable, Detector

    if isinstance(obj, AdjustableTrigger):
        return "trigger"
    if isinstance(obj, Assembly):
        return "assembly"
    if isinstance(obj, Adjustable):
        return "adjustable"
    if isinstance(obj, Detector):
        return "detector"
    return "other"


def reads_as(obj):
    """For an assembly that is itself an item (a MotorRecord: an Assembly
    *and* an Adjustable), "adjustable" or "detector"; else None. The grids
    show such a row with its value and controls next to its link."""
    from eco.elements.protocols import Adjustable, Detector

    if isinstance(obj, Adjustable):
        return "adjustable"
    if isinstance(obj, Detector):
        return "detector"
    return None


def jog_speed_item(obj):
    """The jog speed adjustable of an item with a hardware jog (the motor
    record's JOGF/JOGR with its JVEL: MotorRecord.jog + .jog_speed), else
    None. A software jog (eco.elements.adjustable.jog_option) is not
    offered: it steps through set_target_value, which the tweak view
    already does with a visible step."""
    if not callable(getattr(type(obj), "jog", None)) or not callable(getattr(type(obj), "jog_stop", None)):
        return None
    try:
        speed = object.__getattribute__(obj, "__dict__").get("jog_speed")
    except Exception:
        return None
    if speed is None or not callable(getattr(speed, "get_current_value", None)):
        return None
    return speed


def full_name(obj, fallback=None):
    try:
        return obj.alias.get_full_name()
    except Exception:
        return fallback if fallback is not None else getattr(obj, "name", None) or str(obj)


def relative_name(obj, base):
    try:
        return obj.alias.get_full_name(base=base)
    except Exception:
        return getattr(obj, "name", None) or str(obj)


def enum_labels(obj):
    """Enum labels an item declares itself (AdjustablePvEnum etc.); items
    whose enum only shows in the value report them with each value
    (encode_value's "enum")."""
    strs = getattr(obj, "enum_strs", None)
    if strs:
        try:
            return [str(s) for s in strs]
        except Exception:
            pass
    return None


def has_svg_panel(obj):
    """Whether `obj` can show an SVG panel (Assembly.show's two ways: a
    `_widget_svg_panel` builder or a fixed `_show_svg` file). Looked up
    without attribute access on the instance, which for an Assembly can
    have side effects."""
    if callable(getattr(type(obj), "_widget_svg_panel", None)):
        return True
    try:
        return bool(object.__getattribute__(obj, "__dict__").get("_show_svg"))
    except Exception:
        return False


def _display_and_all(assembly):
    try:
        display_items = list(assembly.display_collection())
    except Exception:
        try:
            display_items = list(assembly.status_collection.get_list(selection="display"))
        except Exception:
            display_items = []
    try:
        all_items = list(assembly.status_collection.get_list())
    except Exception:
        all_items = []
    hidden = [item for item in all_items if not any(item is d for d in display_items)]
    return display_items, hidden


def row_for(item, base=None):
    kind = kind_of(item)
    reads = reads_as(item) if kind == "assembly" else None
    row = {
        "name": full_name(item),
        "label": relative_name(item, base) if base is not None else full_name(item),
        "kind": kind,
        "writable": kind in ("adjustable", "trigger") or reads == "adjustable",
    }
    if reads:
        row["value"] = reads
    labels = enum_labels(item) if kind in ("adjustable", "detector") or reads else None
    if labels:
        row["enum"] = labels
    unit = getattr(item, "unit", None)
    try:
        unit = unit.get_current_value() if hasattr(unit, "get_current_value") else unit
    except Exception:
        unit = None
    if isinstance(unit, str) and unit:
        row["unit"] = unit
    if kind == "trigger":
        row["button"] = getattr(item, "button_label", None) or "▶ Trigger"
        doc = getattr(item, "doc", None)
        if doc:
            row["doc"] = str(doc)
    if kind == "assembly":
        row["svg"] = has_svg_panel(item)
        if camera_info(item):
            row["camera"] = True
    speed = jog_speed_item(item) if (kind == "adjustable" or reads == "adjustable") else None
    if speed is not None:
        row["jog"] = {"speed": full_name(speed)}
    return row


def _register(objects, row, item):
    objects[row["name"]] = item
    if "jog" in row:
        objects[row["jog"]["speed"]] = jog_speed_item(item)


def describe(obj, name=None):
    """(view, objects): the description of `obj` the client renders, and
    {row name: object} for every row in it -- the session keeps the latter
    so later requests address rows by name without re-resolving them."""
    name = name or full_name(obj)
    kind = kind_of(obj)
    view = {"name": name, "title": full_name(obj, name), "kind": kind, "svg": has_svg_panel(obj)}
    cam = camera_info(obj)
    if cam:
        view["camera"] = cam
    objects = {}
    if kind == "assembly":
        display_items, hidden_items = _display_and_all(obj)
        # an assembly that is itself an item (a motor record) lists itself
        # in its own status collection: shown once, as the first row
        display_items = [item for item in display_items if item is not obj]
        hidden_items = [item for item in hidden_items if item is not obj]
        rows = [row_for(item, base=obj) for item in display_items]
        hidden = [dict(row_for(item, base=obj), hidden=True) for item in hidden_items]
        view["rows"] = rows + hidden
        for item, row in zip(display_items + hidden_items, view["rows"]):
            _register(objects, row, item)
        reads = reads_as(obj)
        if reads:
            own = row_for(obj)
            own.pop("svg", None)
            own.pop("value", None)
            own.update(kind=reads, label="value", own=True)
            view["rows"].insert(0, own)
            _register(objects, own, obj)
    else:
        row = row_for(obj)
        view["rows"] = [row]
        _register(objects, row, obj)
    doc = (getattr(type(obj), "__doc__", None) or "").strip()
    if doc:
        view["doc"] = doc.split("\n\n", 1)[0][:500]
    return view, objects
