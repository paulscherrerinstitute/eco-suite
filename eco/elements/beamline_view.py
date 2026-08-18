"""BeamlineView: a generalized, opt-in "view" of an Assembly's components,
ordered/rendered as a beamline diagram, built on `Assembly.mark_beamline()`
tagging rather than `eco.xoptics.beamline_assembly.Beamline`'s own explicit
`add_component()`/`mark_position()` registration.

This is a parallel, additive prototype: the older
`eco.xoptics.beamline_assembly.Beamline` keeps working exactly as before and
is untouched by this module. The intent is for this to eventually replace
it once proven out, at which point the glyph/colour logic duplicated here
(kept intentionally independent of `Beamline` to avoid a xoptics->elements
layering violation while both exist side by side -- `eco.xoptics` already
depends on `eco.elements`, not the other way around) should collapse into
one shared implementation.

Known gaps vs. the older `Beamline` (first-slice scope, not yet addressed):

- No automatic `z_source` <-> `z_sample` derivation (no `z0_source`
  reference-offset concept) -- each `mark_beamline()` call must supply
  whichever frame(s) it wants shown for that position.
- No `kind="zone"` span support (see `Beamline.add_zone_condition`).
- No Gaussian-beam-size fit/plot machinery, no SVG control panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import colorama

from ..utilities.tables import format_table

#: default glyph per `kind` -- see `Assembly.mark_beamline`'s `kind` doc.
#: Deliberately duplicated from `eco.xoptics.beamline_assembly.Beamline`
#: (rather than imported from it) -- see module docstring.
KIND_GLYPHS = {
    "mirror": "\U0001d20e",  # 𝈎
    "mono": "⸗",
    "optic": "⦈",
    "slit": "⌗",
    "attenuator": "\U0001d14d",  # 𝅍
    "profile": "⦿",
    "diagnostic": "◇",
    "timing": "⏱",
    "xspect": "🌈",
    "chopper": "⚈",
    "shutter": "⬒",
    "stage": "▭",
    "valve": "⋈",
    "vacuum": "🌀",
    "gauge": "Ⓟ",
    "pump": "\U0001d546",  # 𝕆
    "ipm": "⟴",
    "sample": "💎",
    "marker": "·",
}

#: `kind`s whose live open/closed state is worth trying to read -- see
#: `Beamline.BLOCKING_KINDS`.
BLOCKING_KINDS = {"shutter", "stopper", "valve"}

#: see `eco.xoptics.beamline_assembly.Beamline.IN_BEAM_COLOR`.
IN_BEAM_COLOR = "\x1b[38;5;208m"


@dataclass
class _BeamlinePosition:
    name: str
    #: one or more membership *paths* -- e.g. `{("fel",)}` or
    #: `{("fel", "front_end"), ("fel-vacuum", "front_end")}` -- see
    #: `_as_type_paths`/`Assembly.mark_beamline`.
    types: frozenset
    z_source: Optional[float] = None
    z_sample: Optional[float] = None
    kind: str = "optic"
    description: str = ""
    #: name of another position registered on the *same* assembly that this
    #: one nests under in `BeamlineView.diagram()` -- see `Assembly.
    #: mark_beamline`. Cross-assembly nesting doesn't need this; it happens
    #: structurally (see `BeamlineView._collect`).
    parent: Optional[str] = None
    z_source_end: Optional[float] = None
    z_sample_end: Optional[float] = None


def _as_type_paths(types):
    """Normalize `types` into a frozenset of path-tuples -- one per
    beamline membership, a component can carry several (see `Assembly.
    mark_beamline`). Each path can have one or more levels, the first being
    the "type" and any further ones organisational "subtypes" (e.g.
    "front_end", "optics", "hutch"), which `BeamlineView`'s dotted attribute
    access (`namespace.beamline.fel.front_end`) navigates by prefix.

    Accepts:
    - a bare string: one single-level path, e.g. ``"fel"`` -> ``{("fel",)}``
    - a tuple of strings: one multi-level path, e.g.
      ``("fel", "front_end")`` -> ``{("fel", "front_end")}``
    - a list of entries, each itself either of the above -- i.e. the list
      is "one entry per membership", so a multi-level path *within* a list
      must be its own nested tuple/list, e.g.
      ``["fel", ("fel-vacuum", "front_end")]``
      -> ``{("fel",), ("fel-vacuum", "front_end")}``, and
      ``[["fel", "front_end"], ["fel-vacuum", "front_end"]]``
      -> ``{("fel", "front_end"), ("fel-vacuum", "front_end")}``.
    """
    if isinstance(types, str):
        return frozenset({(types,)})
    if isinstance(types, tuple):
        return frozenset({types})
    paths = set()
    for entry in types:
        if isinstance(entry, str):
            paths.add((entry,))
        else:
            paths.add(tuple(entry))
    if not paths:
        raise ValueError("need at least one beamline type/path")
    return frozenset(paths)


def register_beamline_position(
    registry, name, types, z_source=None, z_sample=None, kind="optic",
    description="", parent=None, z_source_end=None, z_sample_end=None,
):
    """Build and store a `_BeamlinePosition` into `registry` (an assembly's
    own `_beamline_positions` dict). See `Assembly.mark_beamline`."""
    types = _as_type_paths(types)
    if parent is not None and parent not in registry:
        raise KeyError(
            f"parent='{parent}' for '{name}' is not a registered beamline "
            f"position on this assembly -- call mark_beamline for it first."
        )
    registry[name] = _BeamlinePosition(
        name=name, types=types, z_source=z_source, z_sample=z_sample, kind=kind,
        description=description, parent=parent,
        z_source_end=z_source_end, z_sample_end=z_sample_end,
    )


def _z_of(pos, ref):
    return pos.z_sample if ref == "sample" else pos.z_source


def _resolve(owner, name):
    """Best-effort live component for `name` on `owner`: `None` if not yet
    appended, still lazy-unresolved-and-inaccessible, or any other failure
    -- never raises. Note this *does* trigger resolution of a lazy
    component (attribute access is exactly what a lazy proxy intercepts to
    build on first access) -- same tradeoff `Beamline.diagram()` already
    makes and documents."""
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def _read_open_state(component):
    """Trimmed copy of `Beamline._read_open_state` covering the `is_open`/
    `is_in_beam` shapes (see the in/out convention documented in
    `eco.xoptics.beamline_assembly`) -- extend alongside that method if more
    shapes turn up for `mark_beamline`-tagged components."""
    if component is None:
        return None
    for attr, invert in (("is_open", False), ("is_in_beam", True)):
        sub = getattr(component, attr, None)
        if sub is not None and hasattr(sub, "get_current_value"):
            try:
                value = bool(sub.get_current_value())
                return (not value) if invert else value
            except Exception:
                return None
    return None


def _read_gauge_ok_state(component):
    """See `Beamline._read_gauge_ok_state`."""
    pressure = getattr(component, "pressure", None)
    get_severity = getattr(pressure, "get_severity", None)
    if not callable(get_severity):
        return None
    try:
        severity = get_severity()
    except Exception:
        return None
    if severity == 0:
        return True
    if severity in (1, 2):
        return False
    return None


def _component_value_str(component):
    get_current_value = getattr(component, "get_current_value", None)
    if get_current_value is None:
        return ""
    try:
        return str(get_current_value())
    except Exception:
        return "?"


def _type_char_str(component):
    from .protocols import Adjustable, Detector

    if component is None:
        return ""
    typechar = ""
    if isinstance(component, Adjustable):
        typechar += "✏️"
    elif isinstance(component, Detector):
        typechar += "👁️"
    if hasattr(component, "status_collection"):
        typechar += " ↳"
    return typechar


class BeamlineView:
    """See `Assembly.beamline`/`Assembly.beamline_view`/`Assembly.
    mark_beamline`.

    `prefixes`: a frozenset of path-tuples (see `_as_type_paths`); a
    position matches this view if *any* of its own registered paths
    *starts with* (has as a prefix) *any* of these -- e.g. `prefixes=
    {("fel",)}` matches every "fel"-tagged position regardless of subtype,
    while `{("fel", "front_end")}` matches only that subtype. `None`/empty
    means "match everything" (the root `namespace.beamline` view) -- also
    reachable explicitly as `prefixes={()}`, since every path starts with
    the empty tuple.

    Not constructed directly by user code -- see `Assembly.beamline`
    (dotted attribute navigation, e.g. `namespace.beamline.fel.front_end`)
    and `Assembly.beamline_view(types=...)` (explicit, one-shot).
    """

    def __init__(self, owner, prefixes=None, ref="source", unfold=True):
        self.owner = owner
        self.prefixes = frozenset({()}) if not prefixes else frozenset(prefixes)
        self.ref = ref
        self.unfold = unfold

    def _matches(self, pos):
        for path in pos.types:
            for prefix in self.prefixes:
                if path[: len(prefix)] == prefix:
                    return True
        return False

    def __getattr__(self, name):
        # Only reached when normal lookup fails, i.e. `name` isn't a real
        # attribute/method of this instance -- dotted subtype navigation,
        # e.g. `namespace.beamline.fel.front_end`. Guard dunder/private
        # names so this doesn't intercept pickling/copy/repr-machinery
        # probes (same concern as Namespace's lazy proxy, see
        # eco.utilities.config.Proxy).
        if name.startswith("_"):
            raise AttributeError(name)
        new_prefixes = frozenset(p + (name,) for p in self.prefixes)
        return BeamlineView(self.owner, prefixes=new_prefixes, ref=self.ref, unfold=self.unfold)

    def __dir__(self):
        base = set(super().__dir__())
        for _assembly, pos, _depth in self._collect(all_positions=True):
            for path in pos.types:
                for prefix in self.prefixes:
                    if path[: len(prefix)] == prefix and len(path) > len(prefix):
                        base.add(path[len(prefix)])
        return sorted(base)

    def _collect(self, all_positions=False):
        """Depth-first list of `(owning_assembly, _BeamlinePosition, depth)`
        rows: `self.owner`'s own tagged positions matching `self.prefixes`
        (with `parent=`-nested siblings spliced directly under their
        parent, see `Assembly.mark_beamline`), and -- if `self.unfold` --
        recursing into any matched component that is itself an `Assembly`
        with tagged positions of its own (e.g. a vacuum section, or a KB
        mirror pair exposing its ver/hor foci as real sub-positions).

        `all_positions=True` (used by `__dir__` for subtype discovery)
        skips the `_matches` filter -- every registered position, of any
        type, is included -- while still following the same unfold/nesting
        structure.
        """
        rows = []

        def collect_from(assembly, depth):
            registry = getattr(assembly, "_beamline_positions", {})
            children = {}
            top_level = []
            for pos in registry.values():
                if not all_positions and not self._matches(pos):
                    continue
                if pos.parent is not None and pos.parent in registry:
                    children.setdefault(pos.parent, []).append(pos.name)
                else:
                    top_level.append(pos.name)

            def sort_key(n):
                z = _z_of(registry[n], self.ref)
                return (z is None, z)

            def emit(name, this_depth):
                pos = registry[name]
                rows.append((assembly, pos, this_depth))
                component = _resolve(assembly, name)
                if self.unfold and getattr(component, "_beamline_positions", None):
                    collect_from(component, this_depth + 1)
                for child in sorted(children.get(name, []), key=sort_key):
                    emit(child, this_depth + 1)

            for name in sorted(top_level, key=sort_key):
                emit(name, depth)

        collect_from(self.owner, 0)
        return rows

    def diagram(self):
        from .assembly import FailedComponent

        rows = [r for r in self._collect() if _z_of(r[1], self.ref) is not None]
        unit = "mm" if self.ref == "sample" else self.ref
        prefixes_str = ", ".join(".".join(p) if p else "*" for p in sorted(self.prefixes))
        header = (
            f"BeamlineView('{self.owner.alias.get_full_name()}', {{{prefixes_str}}}) "
            f"({len(rows)} positions, z_{self.ref} [{unit}])"
        )
        if not rows:
            return header + "\n(no matching positions)"
        lines = [header]

        table_rows = []
        beam_present = True
        for assembly, pos, depth in rows:
            z = _z_of(pos, self.ref)
            connector = "│" if beam_present else "┆"
            component = _resolve(assembly, pos.name)
            note = ""
            glyph_color = None
            connector_color = None
            if isinstance(component, FailedComponent):
                glyph, note = "?", f"unavailable: {component.exception}"
            else:
                # component may be None here -- a pure position marker with
                # no control-system object at all (mark_beamline() doesn't
                # require one), same as old Beamline.mark_position(). Not
                # distinguished from "not yet resolved"; _read_open_state/
                # _read_gauge_ok_state(None) safely come back None either
                # way, giving an uncoloured kind glyph, no note.
                glyph = KIND_GLYPHS.get(pos.kind, "▪")
                state = (
                    _read_gauge_ok_state(component)
                    if pos.kind == "gauge"
                    else _read_open_state(component)
                )
                if pos.kind in BLOCKING_KINDS:
                    if state is True:
                        glyph_color = colorama.Fore.GREEN
                    elif state is False:
                        glyph_color = colorama.Fore.RED
                        connector_color = colorama.Fore.RED
                        beam_present = False
                    else:
                        note = "state unknown (no live connection?)"
                elif pos.kind == "gauge":
                    if state is True:
                        glyph_color = colorama.Fore.GREEN
                    elif state is False:
                        glyph_color = colorama.Fore.RED
                else:
                    if state is True:
                        glyph_color = colorama.Fore.GREEN
                    elif state is False:
                        glyph_color = IN_BEAM_COLOR
                        connector_color = IN_BEAM_COLOR
            if glyph_color:
                glyph = glyph_color + glyph + colorama.Style.RESET_ALL
            if connector_color:
                connector = connector_color + connector + colorama.Style.RESET_ALL

            no_status = component is None or isinstance(component, FailedComponent)
            status = "" if no_status else _component_value_str(component)
            typechar = _type_char_str(component)
            indent = "  " * (depth - 1) + "↳ " if depth else ""
            label = f"{indent}{pos.name} [{pos.kind}]"
            if note:
                label += f"  ({note})"
            table_rows.append([f"{z:.2f}", connector, glyph, status, typechar, label])

        lines.append(format_table(
            table_rows, headers=["z", "", "", "status", "type", "component"],
            colalign=["right", "left", "left", "left", "left", "left"],
        ))
        return "\n".join(lines)

    def __repr__(self):
        return self.diagram()
