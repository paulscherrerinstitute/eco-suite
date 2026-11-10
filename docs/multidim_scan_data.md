# Multi-dimensional scan data: grid and mesh

Status: **steps 1, 2 and 4 are implemented** (eco writes the mesh of every
scan, escape reads it as `arr.mesh`, adaptive scans can leave the grid); the
rest is a **proposal**.

## Two words for an N-d space

- A **grid** is the product of one position list per dimension: every
  combination exists (`meshscan`, `scan` with several axes). It is described
  by `grid_specs`, as today, and escape puts its data into an N-d array
  (`Array.grid`, NaN where nothing was measured).
- A **mesh** is the set of points actually measured, wherever they are. A grid
  is a special mesh (a *structured* one); an adaptive scan's refined subset of
  a grid, a path through N-d space, or points anywhere are meshes that are not
  grids (*unstructured*, as in numerics).

So every scan has a mesh, and some scans also have a grid. `meshscan` keeps
its name: it scans a structured mesh, i.e. a grid.

## Why the mesh is needed

The grid alone does not cover:

1. **Points anywhere**: an adaptive 2-D scan following a tilted, narrow ridge
   needs points between grid cells. On a grid, its spacing limits resolution
   everywhere, and a fine grid is mostly empty.
2. **Repeated points**: adaptive scans measure a point again for statistics.
   On the grid, the last value wins. Correct is one point with all its
   measurements, averaged with weights.
3. **Different exposure per point** (`exposure="auto"`): points are not
   equally precise; plots and fits must weight them.
4. **Paths that are not grid lines**: co-moving axes, a line in reciprocal
   space. Today these are 1-D scans whose other coordinates are lost for
   plotting.
5. **Uneven grids** already work (escape's `plot2D` draws cell edges halfway
   between positions), but nothing says whether a grid is regular.

## 1. eco writes the mesh of every scan (implemented)

`scan_info["scan_parameters"]["mesh_specs"]`:

```text
"mesh_specs": {
  "dimension_names": ["xrd.x", "xrd.y"],
  "units": ["mm", "mm"],
  "kind": "adapted",            // "grid", "adapted", "line" or "path"
  "resolution": [0.05, 0.05],   // or null
  "points": [[0.10, -0.20], [0.15, -0.20], ...]
}
```

and per step `scan_step_info[i]["mesh_index"]`: the node it measured.

- `points` are the mesh's **nodes**, in the order they were first measured.
  A point measured twice is one node with two steps, found by index rather
  than by comparing floats. With a `resolution`, targets that round to the
  same multiple of it are the same node.
- `kind`: `grid` (a `grid_specs` exists and every step is a grid point),
  `adapted` (an adaptive scan: a refined subset of its axis grid, `resolution`
  = the grid spacing), `line` (1-D), `path` (several adjustables, no grid).
- The coordinates are the **targets**; readbacks are in `scan_readbacks` as
  before. The dimensions are the grid's dimensions for a grid scan, else one
  per adjustable.
- `grid_specs` stays exactly as it is.

Code: `eco/acquisition/mesh.py`; tests: `tests/test_scan_mesh.py`.

```{admonition} Did you know?
:class: tip

eco already writes the mesh for every scan, so runs taken now will have it
when escape learns to read it. Older runs would be rebuilt from
`scan_values` and `grid_specs` (section 2).
```

## 2. escape: `arr.mesh` (implemented, escape-fel `dev`)

Every escape `Array` has `arr.mesh`, built from `mesh_specs` and the steps'
`mesh_index`, for older runs from `grid_specs` + `grid_index`, or from the scan
parameter values. User guide: escape's `docs/user_guide/mesh.md`.

```python
arr.mesh.mean()                  # one value per node, events of revisits pooled
arr.mesh[:, 3].mean()            # a grid line (grids only)
arr.mesh.as_grid(bins="auto")[:, 3].mean()   # any mesh, binned first
arr.mesh.mean(bins=[ex, ey])     # events binned, then the method per bin
arr.mesh.mean(interpolate=100)   # per node, then linear onto a grid (masked)
arr.mesh.mean(merge=False)       # one per recorded step, in recorded order
arr.mesh.select({"xrd.x": (0, 1)})           # by value, any mesh
```

The results are plain numpy arrays carrying their coordinates (`.coords`,
`.kind`, `.dimension_names`, `.units`, `.edges`, `.mask`; given vectors are
linked, not copied): N-d for a grid, NaN where nothing was measured, otherwise
one value per node. `plot=` works on every method. Pooling the events of a
node weights by number of events, i.e. by exposure. `arr.grid` is unchanged.

Still open here: inverse-variance weights as an option, and fits on meshes
(escape's adaptive-scan models already fit nodes directly).

## 3. eco: adaptive scans leave the grid (implemented)

With 1 and 2, `scans.adaptive_scan(..., mesh="free")`: the axes' step becomes
the mesh `resolution` (the minimum distance at which two points count as one)
instead of a grid, and the strategies get a much finer candidate set (they
already accept any). No `grid_specs` is written then, and the bs counter bins
by step index as for 1-D scans -- which also removes today's "no revisits with
a bs counter on a 2-D grid" rule.

## 4. Regular grids, explicitly

`grid_specs` gets an optional `"regular": [true, false]` per dimension, so
tools can choose `imshow` (fast, regular) or `pcolormesh` (correct, uneven)
without guessing. eco sets it from the axis spec (a step size is regular, a
position list is not).

## Order of work

1. eco writes `mesh_specs` and `mesh_index` for every scan. **Done.**
2. escape: `arr.mesh` with pooled revisits, binning, interpolation and
   plots. **Done** (escape-fel `dev`); fits on meshes still open.
3. escape: a live mesh for `escape.stream`, for live plots of adaptive 2-D
   scans.
4. eco: `adaptive_scan(..., mesh="free")`. **Done**; `scans.optimize`
   records its points the same way ({doc}`optimize`).
5. `regular` flags on `grid_specs`.

Each step is useful on its own, and none breaks existing runs.

## Open questions

- **Target or readback as the coordinate?** Now: targets (exact, and what the
  node merging needs). For a slow or backlash-prone axis the readback is
  where it actually was; escape could offer `arr.mesh(coordinates="readback")`.
- **Weights for averaging**: exposure is always known, per-point errors only
  for some data (monitor/bs feedback, not raw detector images). Proposal:
  exposure by default, inverse variance as an option.
- **Co-moving dimensions**: one dimension listing several adjustables. Its
  coordinate is the first adjustable's position today; it could be a declared
  parameter (path length, momentum transfer).
- **Size**: `points` grows with the nodes. For very long scans (10⁵ steps) it
  belongs in the aux h5 file rather than the json, behind the same accessor.
- **Name of the free option**: decided, `mesh="free"`.
