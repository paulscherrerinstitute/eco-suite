---
added: 2026-11-02
tags: [scans, counters]
details: scanning
---

# Give a scan detectors, not counters

`counters=` takes detectors directly: a bs detector gets its live bs
counter, kept on the detector and reused by every scan, and all other
detectors share one EPICS monitor counter. The first counter sets the
exposure, and the others collect for exactly its window: the same pulses, or
the same time.

```python
scans.ascan(mot, 0, 1, 20, 100, counters=[daq, mon_opt.intensity, diode])
```

More: {doc}`../scanning`
