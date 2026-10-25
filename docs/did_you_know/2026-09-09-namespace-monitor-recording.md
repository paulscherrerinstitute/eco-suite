---
added: 2026-09-09
tags: [scans, daq, data]
details: status_server
---

# Each scan records the history of the whole namespace

With the status server running, every scan now records the update history of
every monitorable channel, not just snapshots at the start and the end. It is
stored next to the run's `status.json`:

```text
<pgroup>/res/run_data/daq/run<NNNN>/aux/namespace_monitor.ixp.h5
```

Switch it off live with `daq.scan_monitoring = False`.

More: {doc}`../status_server`
