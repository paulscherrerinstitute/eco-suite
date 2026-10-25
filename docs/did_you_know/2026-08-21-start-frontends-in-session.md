---
added: 2026-08-21
tags: [ui, shell]
details: launching
---

# Open the desktop or JupyterLab from the session you are in

No need to quit and restart. These open next to your current session without
blocking the prompt:

```python
eco.start_desktop()      # Qt workbench
eco.start_console()      # a second, independent console
eco.start_jupyterlab()   # or eco.start_webapp() for Voila
```

More: {doc}`../launching`
