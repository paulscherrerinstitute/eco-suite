---
added: 2026-08-18
tags: [epics, debugging, expert]
details:
---

# Find the IOC behind a PV, see its console, restart it

`eco.ioc_finder()` opens a window that searches IOCs by name or PV pattern
(via iocinfo.psi.ch, like `cmdt`), shows the boot host, checks whether the
console is reachable, shows its recent output and can restart the IOC.
Controller boxes under `controllers` do the same from the prompt:

```python
eco.ioc_finder()
controllers.wago_gps01.ioc_status()   # also .ioc_host_ping(), .restart_IOC()
```
