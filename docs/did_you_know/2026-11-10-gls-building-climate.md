---
added: 2026-11-10
tags: [archiver, data]
details: archiver_stripchart
---

# Room-climate sensors that exist only in the archiver

A few building-automation temperature/humidity points (PSI's "OSFA" system,
separate from the accelerator control network) are archived but have no
live PV at all — `caget` just times out. `eco.epics_utils.gls` finds and
parses their channel ids; `DetectorArchiverOnly`/`DetectorGls`
(`eco.epics_utils.detector`) read them, with `DetectorGls` falling back from
a live EPICS PV to the archived GLS value when the PV doesn't exist or isn't
connected.

```python
from eco.epics_utils.gls import list_gls_channels
list_gls_channels()  # what's actually bridged -- most guessed ids aren't
```

More: {doc}`../examples/archiver_stripchart`
