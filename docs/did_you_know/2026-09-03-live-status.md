---
added: 2026-09-03
tags: [shell, status]
details:
---

# Watch a device's status update live in the terminal

`live_status()` shows the same table as typing the device's name, but
refreshes it in place, twice a second, until you press `q`, Escape or Ctrl-C.
Useful while something moves, warms up or pumps down.

```python
xrd.live_status()                 # interval=0.5 by default
```
