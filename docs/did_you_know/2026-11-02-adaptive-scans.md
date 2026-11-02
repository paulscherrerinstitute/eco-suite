---
added: 2026-11-02
tags: [scans]
details: adaptive_scans
---

# Let the scan decide where to measure next

`scans.adaptive_scan` starts with a coarse survey and then puts the
next points where the live data say they help most: on the flanks of a peak,
along an edge, across an oscillation. It is an ordinary step scan recorded by
the DAQ; only the choice of the next point is made while it runs.

```python
scans.adaptive_scan([sim.peak.x, -2, 3, 0.02], N_pulses=0.2,
                    feedback=sim.peak.signal, max_points=40)
```

More: {doc}`../adaptive_scans`
