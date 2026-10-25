---
added: 2026-08-18
tags: [scans]
details: scanning
---

# `scans.scan` does meshes of any dimension, with co-moving axes

Each argument is one scan dimension; a *list* of adjustables in one dimension
moves together (a2scan-style). Any number of dimensions, any mix:

```python
scans.scan((mot1, 0, 1, 10), [(mot2, 0, 1, 20), (mot3, 5, 6, 20)], N_pulses=100)
```

An int as the third number is the number of intervals; a float is the step
size.

More: {doc}`../scanning`
