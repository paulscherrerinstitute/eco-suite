---
added: 2026-10-25
tags: [archiver, data]
details: examples/archiver_stripchart
---

# A day of a 100 Hz channel from the archiver in seconds

Long archiver requests are binned on the server automatically (average plus
min/max/count), the way databoard does it: 24 h of one BS channel now takes
about 5 s instead of 23 s and 3 GB. Short ranges stay raw, and a warning
names any binned channel.

```python
archiver.get_data_time_range(mon_mono.intensity, start=-86400)            # auto-binned
archiver.get_data_time_range(mon_mono.intensity, start=-3600, max_bins=None)  # always raw
```

More: {doc}`../examples/archiver_stripchart`
