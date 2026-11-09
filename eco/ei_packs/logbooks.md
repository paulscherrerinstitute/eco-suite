---
title: Logbooks from the session
words: [elog, scilog, logbook, logbooks, post, posting, screenshot, entry, entries, message to the elog]
---
- `elog` posts to the logbooks of the experiment (the pgroup's SciLog
  logbook): `elog.post("text", Path("plot.png"), tags=[...])` -- texts
  (Markdown) and files (pathlib.Path, uploaded) as positional arguments;
  `pgroup=` posts to another pgroup's logbook once.
  `scilog.screenshot()` posts a screenshot.
- Scans and runs post their own entries (run number, command, scan info);
  nothing needs doing for that.
- Reading logbooks from the session: `eco.start_scilog()` opens the SciLog
  viewer in the browser (search, outline, writing with a personal token);
  `%ei` searches the indexed SciLog and elog entries.
- Code in old logbook posts may use names that eco no longer has;
  `%ei --check-logs` lists them.

```python
from pathlib import Path
elog.post("aligned the KB, see the attached scan", tags=["alignment"])
```
