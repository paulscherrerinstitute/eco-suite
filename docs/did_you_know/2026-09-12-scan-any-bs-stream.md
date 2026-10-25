---
added: 2026-09-12
tags: [scans, bs]
details: scanning
---

# Any bs stream can be scanned directly, ratios included

```python
mon_opt.intensity.stream.scans.ascan(mot, 0, 1, 20, 100)
(s1 / s2).scans.dscan(mot, -1, 1, 20, 100)
```

The beam-synchronous data is binned per step, live-plotted, and stored as
escape Arrays at the end of the scan. For mesh scans,
`BsStreamCounter(...).grid()` gives the N-D binned result, live.

More: {doc}`../scanning`
