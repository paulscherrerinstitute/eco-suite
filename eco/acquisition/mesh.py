"""The mesh of a scan: where in its N-d space each step measured.

Every scan has one, written to ``scan_info["scan_parameters"]["mesh_specs"]``
and, per step, ``scan_step_info[i]["mesh_index"]``. A *grid* is a special,
structured mesh -- the product of one position list per dimension, described
as before by ``grid_specs`` next to it. A mesh in general is just the set of
points measured: an adaptive scan's refined subset of a grid, a path through
N-d space, or points anywhere. See docs/multidim_scan_data.md.

``mesh_specs``::

    {"dimension_names": ["xrd.x", "xrd.y"],
     "units": ["mm", "mm"],
     "kind": "grid" | "adapted" | "line" | "path",
     "resolution": [0.05, 0.05] or None,
     "points": [[x0, y0], [x1, y1], ...]}

``points`` are the mesh's nodes in the order they were first measured;
``mesh_index`` of a step is its node, so a point measured twice is one node
with two steps -- found by index, not by comparing floats. Targets that round
to the same multiple of the ``resolution`` in every dimension are the same node
(without a resolution: equal targets).
"""

import math


def _unit(adjustable):
    try:
        unit = adjustable.unit
        if hasattr(unit, "get_current_value"):
            unit = unit.get_current_value()
        return str(unit) if unit else ""
    except Exception:
        return ""


def _name(adjustable):
    try:
        return adjustable.alias.get_full_name()
    except Exception:
        return getattr(adjustable, "name", str(adjustable))


class ScanMesh:
    """The nodes measured so far, and what they are points of."""

    def __init__(self, dimension_names, units, kind, resolution=None):
        self.specs = {
            "dimension_names": list(dimension_names),
            "units": list(units),
            "kind": kind,
            "resolution": None if resolution is None else [float(r) for r in resolution],
            "points": [],
        }
        self._index = {}  # node key -> node index

    @property
    def kind(self):
        return self.specs["kind"]

    @kind.setter
    def kind(self, kind):
        self.specs["kind"] = kind

    @property
    def resolution(self):
        return self.specs["resolution"]

    @resolution.setter
    def resolution(self, resolution):
        self.specs["resolution"] = None if resolution is None else [float(r) for r in resolution]
        self._index = {self._key(p): i for i, p in enumerate(self.specs["points"])}

    def _key(self, point):
        resolution = self.specs["resolution"]
        if not resolution:
            return tuple(point)
        return tuple(
            round(x / r) if r else x for x, r in zip(point, resolution)
        )

    def add(self, point):
        """The node index of `point` (a new node if it is not one yet)."""
        point = [float(x) for x in point]
        key = self._key(point)
        index = self._index.get(key)
        if index is None:
            index = self._index[key] = len(self.specs["points"])
            self.specs["points"].append(point)
        return index


def mesh_for_scan(adjustables, gridspecs=None):
    """The mesh of a scan over `adjustables` (with its `gridspecs`, if it is
    a grid scan): the grid's dimensions, else one per adjustable."""
    if gridspecs:
        names = list(gridspecs.get("grid_dimension_names") or [])
        ndim = len(gridspecs["shape"])
        names += [f"dim_{d}" for d in range(len(names), ndim)]
        units = [_unit(a) for a in adjustables] if len(adjustables) == ndim else [""] * ndim
        return ScanMesh(names, units, "grid")
    kind = "line" if len(adjustables) == 1 else "path"
    return ScanMesh([_name(a) for a in adjustables], [_unit(a) for a in adjustables], kind)


def step_point(mesh, values, gridspecs=None, grid_index=None):
    """The current step's point on `mesh`: the grid positions of its
    ``grid_index`` for a grid, else the adjustables' targets. None if a
    target is not a number (nothing to place)."""
    if gridspecs and grid_index is not None:
        return [float(gridspecs["positions"][d][i]) for d, i in enumerate(grid_index)]
    try:
        point = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if len(point) != len(mesh.specs["dimension_names"]) or not all(map(math.isfinite, point)):
        return None
    return point
