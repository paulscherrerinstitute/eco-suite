---
added: 2026-11-10
tags: [scans]
details: adaptive_scans
---

# Adaptive scans between the grid points

With `mesh="free"` an adaptive scan is no longer tied to its grid: it can put
points between grid cells, e.g. along a tilted, narrow ridge. The axis step
becomes the resolution (closer points count as one), and escape loads the run
with `arr.mesh` as usual.

```python
scans.adaptive_scan([sim.peak2d.x, -2, 2, 0.2], [sim.peak2d.y, -2, 2, 0.2], exposure_def=0.2,
                    feedback=sim.peak2d.signal, strategy="peak2d", max_points=60, mesh="free")
```

More: {doc}`../adaptive_scans`
