# Proposal: multi-dimensional scan data beyond the grid

Status: **proposal**, nothing of this is implemented. For eco and escape
together.

## Why

Today a scan is a list of steps. A multi-dimensional scan additionally
declares a **grid**: `grid_specs` with `shape`, one position array per
dimension, `index_plan`, and a `grid_index` per step. escape uses that to put
the data into an N-d array (`Array.grid`, NaN where nothing was measured) and
to plot 2-D data as a map. That covers mesh scans, and adaptive scans as long
as they stay on a grid.

It does not cover:

1. **Points anywhere**: an adaptive 2-D scan that follows a tilted, narrow
   ridge needs points between grid cells; on a grid, the grid spacing limits
   resolution everywhere, and a fine grid is mostly empty.
2. **Repeated points**: adaptive scans measure a point again for more
   statistics. On the grid, the last value wins. Proper treatment is a
   weighted average (by exposure or inverse variance), with the individual
   measurements kept.
3. **Different exposure per point** (`exposure="auto"`): points are not
   equally precise; plots and fits must weight them.
4. **Paths that are not grid lines**: co-moving axes (`a2scan`), a scan along
   a direction in reciprocal space, or a line in a 2-D plane. Today these are
   1-D scans whose second coordinate is lost for plotting.
5. **Readbacks instead of targets**: a slow or backlash-prone axis ends up
   near, not on, its target. For a scattered layout the actual position is the
   coordinate; on a grid it is rounded away.
6. **Uneven grids** already work (positions are arrays and escape's `plot2D`
   draws cell edges halfway between positions), but nothing says whether a
   grid is "regular", so tools cannot rely on it.

## What a solution must keep

- Every existing run keeps loading as it does now. `grid_specs` stays valid
  and keeps meaning what it means today.
- Data of one step stay together (one step = one acquisition = one DAQ
  file set), as now.
- Small: the description lives in `scan_info` (json) next to the step list,
  not in a new file type.
- Plotting a 2-D scan stays one call, whatever the layout.

## Proposal

### 1. Every step is a point, and the scan says so

eco adds one block to `scan_info["scan_parameters"]`, written for **every**
scan (it costs a few numbers per step):

```text
"points": {
  "dimensions": [
    {"name": "xrd.x", "unit": "mm", "adjustables": ["xrd.x"]},
    {"name": "xrd.y", "unit": "mm", "adjustables": ["xrd.y"]}
  ],
  "layout": "grid",
  "target":   [[0.10, -0.20], [0.15, -0.20], ...],
  "readback": [[0.1003, -0.1998], ...],
  "exposure": [100, 100, 200, ...],
  "visit":    [0, 0, 0, 1, ...]
}
```

- `dimensions`: the scan's coordinates. A co-moving group is **one**
  dimension listing several adjustables (its coordinate is the first one, or a
  declared parameter such as a path length).
- `layout`: a hint for tools, one of `"line"` (1-D, ordered),
  `"grid"` (a `grid_specs` exists), `"path"` (a curve through N-d, ordered by
  step: co-moving or reciprocal-space scans), `"scattered"` (points anywhere,
  order meaningless: adaptive).
- `target` / `readback`: per step, per dimension. Today's
  `scan_values`/`scan_readbacks` hold the same numbers per adjustable; this
  is the same information per *dimension*, which is what plotting needs.
- `exposure`: per step, in the unit of the counters.
- `visit`: how many times this point was measured before (0 = first).

`grid_specs` stays as it is and is written exactly when `layout == "grid"`.

### 2. escape: a point table on every scan, grids as a view

escape's `Scan` gets a `points` table built from this block (from
`scan_values` + `grid_specs` for old runs, so everything loaded today has it
too). On top of it, three ways to get regular arrays, all explicit:

| Call | Returns | For |
|---|---|---|
| `arr.on_grid(reduce="weighted")` | the existing N-d grid view, revisits **averaged** (weights: exposure or 1/error²) instead of last-wins | grid layouts |
| `arr.binned({"xrd.x": edges, ...})` | N-d histogram means with counts, for any layout | scattered, path |
| `arr.interpolated(grid, method="linear")` | values on a regular grid by interpolation, with a mask for points too far from any measurement | maps of scattered data |

and plotting that follows `layout` automatically:

- `grid`: `pcolormesh` (as today);
- `scattered`: points coloured by value over a triangulated map
  (`tricontourf`), so nothing is invented beyond the convex hull;
- `path`: value against path length, with the other coordinates as a
  secondary axis or colour.

A fit does not need a grid at all. escape's adaptive-scan models already fit
scattered points directly with their errors (`fit_peak2d(points, y, yerr)`).
They should be offered for any `Array` with a points table, so "where is the
peak" never depends on the layout.

### 3. eco: adaptive scans leave the grid

With 1 and 2 in place, `scans.adaptive_scan` gets a `layout="scattered"`
option. The axes' step then becomes a **minimum distance** between points
(below it two points count as the same point) instead of a grid. Strategies
already accept arbitrary candidate positions, so they can be given a much
finer candidate set. The step's `grid_index` is then not written. For
`BsStreamCounter`, bins are keyed by step index (as for 1-D scans), which
also removes today's "no revisits with a bs counter on a 2-D grid" limit.

### 4. Uneven grids, explicitly

`grid_specs` gets an optional `"regular": [true, false]` per dimension, so
tools can choose `imshow` (fast, regular) or `pcolormesh` (correct, uneven)
without guessing. eco sets it from the axis spec (a step size is regular, a
position list or lin/log anchors are not). The adaptive strategies already
take uneven candidate sets (`candidates=`); their own `grid_step=` option is
regular only, which is fine since eco passes candidates.

## Order of work

1. eco writes `points` for every scan. Additive, no reader needs it yet.
2. escape builds `Scan.points` (also for old runs) and `on_grid(reduce=...)`.
   This alone fixes repeated points of today's on-grid adaptive scans.
3. escape: `binned`, `interpolated`, layout-aware plotting, fits on points.
4. eco: `adaptive_scan(..., layout="scattered")`.
5. `regular` flags on `grid_specs`.

Each step is useful on its own, and none breaks existing runs.

## Open questions

- **Which coordinate for scattered data, target or readback?** Proposal:
  readback when it exists and is valid, target otherwise, with the choice
  recorded in `points`.
- **Weights for averaging**: exposure is always known; per-point errors only
  for some data (monitor/bs feedback, not raw detector images). Default
  exposure, option inverse variance.
- **Co-moving dimension coordinate**: first adjustable, or a declared
  parameter (path length, momentum transfer)? Probably the scan should be able
  to name it.
- **Size**: `points` duplicates `scan_values`/`scan_readbacks` arranged by
  dimension. For very long scans (10⁵ steps) it belongs in the aux h5 file
  rather than the json, behind the same accessor.
