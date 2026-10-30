---
added: 2026-09-20
tags: [memory, presets, alignment]
details:
---

# Which saved state is the device closest to right now?

`get_memory_status()` ranks the saved memories of a device by how many values
match the live state, within each value's learned tolerance, and shows the
biggest remaining difference. `recall_status_str()` says whether you are
still at the state you last recalled, or where you drifted.

```python
xrd.memory.get_memory_status(top_n=5)
print(xrd.memory.recall_status_str())
```
