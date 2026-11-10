"""The frame/tool hierarchy clients browse and select from.

A tree is a dict with two kinds, each a nested dict of VAL3 symbol -> children::

    {"frames": {"world": {"f_SwissFELCoord": {...}}},
     "tools":  {"t_flange": {"t_Adapter": {...}}}}

It is read from a JSON file the server owns, rather than from the controller,
so that ad-hoc changes to the VAL3 program never silently change what users are
offered -- the file is the source of truth, and the server only *checks* it
against the controller (see :meth:`~eco.robots.bernina.BerninaRobot.check_tree`).

Pure functions, no eco imports: the eco client renders with the same code.
"""

from __future__ import annotations

import json
from pathlib import Path

KINDS = ("frames", "tools")


def from_chains(chains):
    """Build a tree from dotted parent chains (the old ``FRAME_HIERARCHY``).

    A chain whose root starts with ``t_`` is a tool, anything else a frame.
    """
    tree = {kind: {} for kind in KINDS}
    for chain in chains:
        names = [n for n in chain.split(".") if n]
        if not names:
            continue
        node = tree["tools" if names[0].startswith("t_") else "frames"]
        for name in names:
            node = node.setdefault(name, {})
    return tree


def validate(tree):
    """Raise ValueError on a malformed tree; return it unchanged otherwise."""
    if not isinstance(tree, dict) or set(tree) != set(KINDS):
        raise ValueError(f"a tree needs exactly the keys {list(KINDS)}")
    for kind in KINDS:
        seen = set()

        def walk(node, where):
            if not isinstance(node, dict):
                raise ValueError(f"{kind}: children of {where} must be a dict")
            for name, children in node.items():
                if not name or "." in name:
                    raise ValueError(f"{kind}: invalid symbol {name!r}")
                if name in seen:
                    raise ValueError(f"{kind}: {name!r} appears twice")
                seen.add(name)
                walk(children, name)

        walk(tree[kind], "the root")
    return tree


def load(path):
    return validate(json.loads(Path(path).read_text()))


def flatten(tree, kind):
    """``[(depth, name, dotted_path, lasts)]`` in display order.

    ``lasts`` holds one "is the last child" flag per depth, which is what
    drawing the connectors needs.
    """
    rows = []

    def walk(node, depth, prefix, lasts):
        items = list(node.items())
        for i, (name, children) in enumerate(items):
            path = f"{prefix}.{name}" if prefix else name
            flags = lasts + (i == len(items) - 1,)
            rows.append((depth, name, path, flags))
            walk(children, depth + 1, path, flags)

    walk(tree[kind], 0, "", ())
    return rows


def names(tree, kind):
    return [name for _, name, _, _ in flatten(tree, kind)]


def path_of(tree, kind, name):
    """Dotted path of a bare symbol; the symbol itself if it is not in the tree."""
    for _, row_name, path, _ in flatten(tree, kind):
        if row_name == name:
            return path
    return name


def bare(name):
    """``"t_flange.t_Adapter.t_JF01T03"`` -> ``"t_JF01T03"``."""
    return str(name).split(".")[-1]


def render(tree, kind, active=None, missing=(), cursor=None):
    """Text lines of one kind, with connectors.

    ``●`` marks the active symbol, ``>`` the cursor, and missing symbols get a
    ``(missing on controller)`` suffix.
    """
    active = bare(active) if active else None
    lines = []
    for depth, name, _, flags in flatten(tree, kind):
        # Roots carry no connector, so their own flag draws nothing.
        indent = "".join("   " if last else "│  " for last in flags[1:-1])
        connector = "" if depth == 0 else ("└─ " if flags[-1] else "├─ ")
        mark = "●" if name == active else " "
        pointer = ">" if name == cursor else " "
        suffix = "  (missing on controller)" if name in missing else ""
        lines.append(f"{pointer}{mark} {indent}{connector}{name}{suffix}")
    return lines
