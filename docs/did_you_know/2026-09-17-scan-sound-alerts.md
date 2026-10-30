---
added: 2026-09-17
tags: [scans, daq, alerts]
details:
---

# Hear when a scan waits for beam, and when it goes on

With sound alerts on, a cowbell rings when the checker finds the beam bad and
the scan waits, and a posthorn plays when it resumes. You can also have the
run number spoken at the end of each scan. Both are off by default and can be
switched at any time:

```python
daq.sound_alerts = True
daq.speak_run_number = True
```
