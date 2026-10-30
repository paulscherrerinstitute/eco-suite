---
added: 2026-09-13
tags: [namespace, experiment, productivity]
details:
---

# A device you add during the beamtime is there again after a restart

Append with `add_patch=True` and eco saves the input that built it to your
pgroup's `res/eco/patches.py`, which is replayed the next time the namespace
loads. Earlier inputs it depends on (`x = ...`) are replayed too, side effects
included. Use `patch_replay_origin=False` to only quote them as comments.

```python
xrd._append(MotorRecord, "SARES20-MF1:MOT_5", name="pin_y", add_patch=True)
```
