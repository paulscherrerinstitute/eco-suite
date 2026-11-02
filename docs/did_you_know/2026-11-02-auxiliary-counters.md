---
added: 2026-11-02
tags: [scans, counters]
details: scanning
---

# A live counter in every scan, next to the DAQ

`scans.append_counter()` adds a detector to the counters every scan uses by
default. It follows the DAQ's window, which gives a live plot of exactly the
shots the DAQ recorded. `counter_options` gives one counter its own
acquisition instead.

```python
scans.append_counter(mon_opt.intensity)
scans.clear_auxiliary_counters()
```

More: {doc}`../scanning`
