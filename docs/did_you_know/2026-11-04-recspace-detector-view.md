---
added: 2026-11-04
tags: [diffraction, recspace]
details: recspace
---

# See what the detector sees before you move

The 3D view of the reflections browser has detector presets, Bernina's
JF01T03 and JF07T32 among them, in both orientations. It draws the cut of
the Ewald sphere on the detector, the cells and Brillouin zones around the
reflection, and everything else that falls onto the detector at that
position.

```python
si.widget_reflections(1, 1, 1)
```

More: {doc}`../recspace`
