---
added: 2026-11-02
tags: [scans, testing]
details: simulated_targets
---

# Practise scans on a peak that isn't there

`sim` in the namespace has simulated peaks, edges, a 2-D peak and
pump-probe oscillations, with motors and detectors that behave like a
stepper, a piezo, a diode or a lock-in: settling, backlash, lag, noise,
drift. Move the peak while the scan runs to see how it copes.

```python
scans.ascan(sim.peak.x, -2, 2, 40, 0.2, counters=[sim.peak.signal])
sim.peak.center.mv(1.2)
```

More: {doc}`../simulated_targets`
