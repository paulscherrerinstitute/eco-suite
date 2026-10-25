---
added: 2026-10-25
tags: [ui, cameras, plots]
details: live_gui
---

# Camera viewers and plots keep updating while a command runs

Open GUI windows no longer freeze during `umv`, a scan or `sleep`. eco
services them while your command runs: measured 0 → 19–20 frames/s on a
20 Hz camera viewer. In the desktop, Ctrl-C now also reaches a running cell.

```python
from eco.utilities import mainthread
mainthread.status()          # what it has been doing; ECO_NO_HEARTBEAT=1 switches it off
```

More: {doc}`../live_gui`
