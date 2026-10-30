---
added: 2026-10-25
tags: [scans, queues, productivity]
details: queues
---

# Queue scans and keep your prompt

`%queue` runs commands one after another on their own thread, so you can
look at data or queue more while they work. Every task is test-run when you
queue it: typos, wrong arguments and scan positions outside the limits are
caught before anything moves.

```python
%queue scans.ascan(mot, 0, 1, 50, 0.1)
%queue                                   # running, pending and recent tasks
```

More: {doc}`../queues`
