---
added: 2026-11-05
tags: [shell, namespace, devices]
details: developer_notes
---

# Pick up edited device code without restarting the session

After a device's code was changed (and pulled), rebuild just that device from
the new code. It reloads the module the device's class lives in and rebuilds
the device. A device that failed at startup can be retried without the reload.
Arguments written in the instrument definition itself (e.g. channel PVs passed
in `bernina_beamline_hutch.py`) are not re-read: those need a new session.

```python
namespace.reinitialize("att_usd", "clic", reload_modules=True)
```

More: {doc}`../developer_notes`
