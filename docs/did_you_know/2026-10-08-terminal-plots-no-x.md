---
added: 2026-10-08
tags: [plots, ssh]
details: terminal_plots
---

# No display? Plots are drawn as text in the terminal

Over plain ssh without X, or with `ECO_NO_X=1` set, eco opens no Qt windows,
and the tweak detector plot is drawn right in the terminal instead.

```bash
ECO_NO_X=1 eco
```

More: {doc}`../terminal_plots`
