---
added: 2026-08-18
tags: [hardware, motors]
details: examples/direct_motor_drivers
---

# Drive a Newport XPS or Schneider MCode motor without an IOC

For bench tests and commissioning, eco can talk to these controllers
directly: over TCP for the XPS, over serial for MCode (including multi-drive
party mode). No EPICS is involved.

```python
from eco.devices_general.newport_xps import NewportXPS
with NewportXPS("192.168.0.254") as xps:
    xps.initialize("Group1"); xps.home("Group1"); xps.move_absolute_and_wait("Group1", 10.0)
```

More: {doc}`../examples/direct_motor_drivers`
