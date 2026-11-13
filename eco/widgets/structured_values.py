"""Dict/list values (``config_bernina.gps_config``, a camera configuration,
...) for the value editors of the front-ends: the Qt grid's dialog
(eco.widgets.dict_editor_qt) and the web app (eco.web). No GUI imports.

A value is edited as a tree of dicts and lists with plain leaves (None, bool,
int, float, str). Whatever else sits in it (a numpy array, an object) is
shown by its repr and kept unchanged: it is *read-only* in the editors.
Writing back restores what a JSON round trip or a text field loses against
the value that was read: an int typed into a float leaf stays a float, a
tuple stays a tuple, numpy scalars become the same numpy type again.
"""

import ast
import json
import numbers

import numpy as np

PLAIN = (type(None), bool, int, float, str)


def is_structured(value):
    """Whether a value is shown through the dict/list editor."""
    return isinstance(value, (dict, list, tuple))


def _plain_scalar(value):
    """A plain Python leaf for `value`, or None if it is not one (numpy
    scalars count as plain: they are numbers or bools)."""
    if isinstance(value, PLAIN):
        return value
    if isinstance(value, np.generic) and value.dtype.kind in "biuf":
        return value.item()
    return None


def is_plain_leaf(value):
    return isinstance(value, PLAIN) or (
        isinstance(value, np.generic) and value.dtype.kind in "biuf"
    )


def to_jsonable(value):
    """(data, read_only_paths): `value` as JSON-able data (dicts with str
    keys, lists, plain leaves); a leaf that is none of those becomes its
    repr, and its path (a list of keys/indices) is listed as read-only."""
    read_only = []

    def walk(item, path):
        if isinstance(item, dict):
            out = {}
            for key, sub in item.items():
                if not isinstance(key, str):
                    read_only.append(path + [str(key)])
                    out[str(key)] = repr(sub)
                    continue
                out[key] = walk(sub, path + [key])
            return out
        if isinstance(item, (list, tuple)):
            return [walk(sub, path + [i]) for i, sub in enumerate(item)]
        if is_plain_leaf(item):
            plain = _plain_scalar(item)
            if isinstance(plain, float) and not np.isfinite(plain):
                read_only.append(path)
                return repr(plain)
            return plain
        read_only.append(path)
        return repr(item)

    return walk(value, []), read_only


def type_name(value):
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, tuple):
        return "tuple"
    if isinstance(value, list):
        return "list"
    if value is None:
        return "None"
    return type(value).__name__


def coerce_like(new, old):
    """`new` (edited data) with the types of `old` (the value that was
    read) restored where the structure matches; new entries keep their own
    types. A read-only leaf left as its repr gets the original back."""
    if isinstance(old, dict) and isinstance(new, dict):
        lookup = {str(k): k for k in old}
        out = {}
        for key, sub in new.items():
            original_key = lookup.get(key, key)
            out[original_key] = coerce_like(sub, old[original_key]) if original_key in old else sub
        return out
    if isinstance(old, (list, tuple)) and isinstance(new, (list, tuple)):
        items = [coerce_like(sub, old[i]) if i < len(old) else sub for i, sub in enumerate(new)]
        return tuple(items) if isinstance(old, tuple) else items
    if not is_plain_leaf(old):
        # read-only: the editors show its repr; unchanged means unchanged
        if new is old or (isinstance(new, str) and new == repr(old)):
            return old
        return new
    return _coerce_leaf(new, old)


def _coerce_leaf(new, old):
    if isinstance(new, (dict, list, tuple)) or new is None or old is None:
        return new
    try:
        if isinstance(old, (bool, np.bool_)):
            value = parse_bool(new)
        elif isinstance(old, numbers.Integral):
            value = int(new) if not isinstance(new, float) or new.is_integer() else new
            if isinstance(value, float):
                return value  # typed a fraction into an int: keep what was typed
        elif isinstance(old, numbers.Real):
            value = float(new)
        elif isinstance(old, str):
            value = new if isinstance(new, str) else str(new)
        else:
            return new
    except (TypeError, ValueError):
        return new
    if isinstance(old, np.generic):
        return type(old)(value)
    return value


def parse_bool(text):
    if isinstance(text, bool):
        return text
    if isinstance(text, numbers.Number):
        return bool(text)
    low = str(text).strip().lower()
    if low in ("true", "1", "yes", "on"):
        return True
    if low in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {text!r}")


def parse_leaf(text, old=None, kind=None):
    """A leaf typed as text: as the type of `old` (or `kind`, a type_name)
    when given, else as a Python literal (``1``, ``2.5``, ``True``,
    ``None``, ``"x"``, ``[1, 2]``, ``{"a": 1}``), JSON (``true``, ``null``)
    or, failing all of those, the text itself. Raises ValueError when the
    text is not valid for the expected type."""
    kind = kind or (type_name(old) if old is not None else None)
    if kind in ("bool", "bool_"):
        return parse_bool(text)
    if kind in ("int", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"):
        number = float(text)
        if not number.is_integer():
            raise ValueError(f"not an integer: {text!r}")
        return int(number)
    if kind in ("float", "float16", "float32", "float64"):
        return float(text)
    if kind == "str":
        return text
    for parse in (ast.literal_eval, json.loads):
        try:
            return parse(text)
        except Exception:
            pass
    return text
