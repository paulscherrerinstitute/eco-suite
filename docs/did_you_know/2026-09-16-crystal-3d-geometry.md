---
added: 2026-09-16
tags: [diffraction, ui]
details:
---

# See your crystal's diffraction geometry in 3D

Open a browser view of the diffractometer and reciprocal-space geometry: the
current position plus every stored reference reflection, clickable. It is
computed from the crystal's UB matrix, so there is nothing to enter by hand.

```python
diffcalc.<your_crystal>.widget_geom()
```
