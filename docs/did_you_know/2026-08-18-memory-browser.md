---
added: 2026-08-18
tags: [ui, memory, presets]
details:
---

# Browse, compare and recall saved device states in a window

The **memories** button on a device panel opens a browser for its saved
states: filter them, see what differs from now, recall, export, and tag one
as a named preset. It can also show states saved on a parent assembly that
cover this device. In the terminal:

```python
xrd.memory.memorize("aligned on sample 3", preset_varname="sample3")
xrd.memory()              # pick one to recall; xrd.memory.presets.sample3
```
