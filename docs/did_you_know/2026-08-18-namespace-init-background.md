---
added: 2026-08-18
tags: [namespace, startup]
details:
---

# `init_all()` runs in the background, so keep working

`namespace.init_all()` now initializes the namespace in the background by
default, so your prompt stays usable. Check how far it has got without
touching anything:

```python
namespace.init_all()
namespace.init_progress()   # {'running': True, 'total': ..., 'initialized': ..., 'failed': ..., 'pending': ...}
namespace.read_init_log()   # the initialization messages, captured instead of printed
```
