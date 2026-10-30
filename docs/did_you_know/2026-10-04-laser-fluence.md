---
added: 2026-10-04
tags: [laser, calculation]
details:
---

# Laser fluence on the sample, grazing incidence included

`las.fluence()` gives the fluence in mJ/cm² from the spot diameters (m), the
pulse energy (J) and the incidence angle to the sample surface (degrees,
default 5). The spot's elongation along the surface at grazing incidence is
taken into account.

```python
las.fluence(200e-6, 100e-6, 10e-6, alpha=5)
```
