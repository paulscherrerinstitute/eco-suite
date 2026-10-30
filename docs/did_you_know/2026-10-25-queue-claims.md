---
added: 2026-10-25
tags: [scans, queues, safety]
details: queues
---

# A queued scan's motors can't be moved from elsewhere by accident

While a queue task runs, the adjustables it uses are claimed by it. A move of
one of them from the prompt, a GUI panel or another queue is stopped before
anything moves, with a message naming the task. `stop()` is never blocked.

```python
with forced():
    mot.mv(0.5)           # move it anyway, e.g. to fix a stuck move; logged
```

More: {doc}`../queues`
