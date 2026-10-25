---
added: 2026-09-06
tags: [epics, reliability]
details: channel_access
---

# PV reads come from monitors: faster, and no more silent `None`

PVs are now monitored by default, so a read is a cache lookup, about 30×
faster and unable to time out to `None` in the middle of a scan. Channels
updating faster than 10 Hz are detected and un-monitored automatically.

```python
from eco.epics_utils import ca_tuning
ca_tuning.monitor_report()
```

If a `None` still happens, a warning names the PV and the likely cause.

More: {doc}`../channel_access`
