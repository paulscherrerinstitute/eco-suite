---
added: 2026-08-21
tags: [startup, ui]
details: launching
---

# One command for every eco front-end, and it remembers your choice

```bash
eco desktop -s bernina                     # Qt workbench: console + Namespace browser
eco jupyterlab -s bernina --set-rcfile     # ...and make it what a bare `eco` does
```

`console`, `desktop`, `jupyterlab` and `webapp` are all subcommands of the same
command. `--set-rcfile` stores the choice in `~/.ecorc`. From a checkout, use
`scripts/eco-dev` with the same arguments.

More: {doc}`../launching`
