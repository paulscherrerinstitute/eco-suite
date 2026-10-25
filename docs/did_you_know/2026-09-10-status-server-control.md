---
added: 2026-09-10
tags: [daq, status]
details: status_server
---

# Check and restart the status server from your session

```python
status_server.status()     # health, failed devices, recent requests
status_server.reinit()     # retry what failed
status_server.gui()
```

From any shell, `eco-status-server respawn -w` restarts the real server
wherever it runs. eco also refuses to start a second, competing server.

More: {doc}`../status_server`
