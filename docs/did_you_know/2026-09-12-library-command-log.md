---
added: 2026-09-12
tags: [shell, history]
details: command_history
---

# Your own scripts and notebooks can log their commands too

Using eco as a library (`from eco import bernina`, no startup script)? One
call gives that session the same command log as the eco consoles, so it shows
up in the Log Viewer and in `...h` history. It is labelled with your identity,
so colleagues on a shared account stay apart.

```python
from eco import globallogger
globallogger.start()          # optional: label="my analysis"
```

More: {doc}`../command_history`
