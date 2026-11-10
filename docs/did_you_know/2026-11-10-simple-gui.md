---
added: 2026-11-10
tags: [ui, desktop, startup, gadgets]
details: simple_gui
---

# A control panel without code

In eco desktop, **⋯ → Add indicator → Dial / knob** on a grid row docks a
knob for that value next to the grid; LEDs, gauges, numeric tiles and strip
charts work the same way. Close everything else, arrange the rest, and
**Workspace → Save Startup Script...** writes one `.sh` that opens just that
panel, initializing only the devices it shows. **Tools → Edit Gadgets in Qt
Designer...** lays the gadgets out freely.

More: {doc}`../simple_gui`
