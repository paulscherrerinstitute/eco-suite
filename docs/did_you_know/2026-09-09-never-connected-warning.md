---
added: 2026-09-09
tags: [namespace, debugging, epics]
details: channel_access
---

# eco tells you when a device built fine but never connected

A component whose IOC is down used to look normal until a read returned
`None`. Now an assembly's status starts with a yellow **NEVER CONNECTED**
line naming such components, next to the red line for ones that failed to
build. A sub-assembly that is entirely dead is listed once, by its own name.

```python
xrd.get_disconnected_components()   # {"hex": [pv names], ...}
```

More: {doc}`../channel_access`
