---
added: 2026-05-26
tags: [namespace, debugging]
details:
---

# A failed component tells you how to build it by hand

When a namespace component fails to initialize, its error now ends with a
ready-to-paste line that rebuilds exactly that component, so you can reproduce
and debug the failure directly:

```text
For manual instantiation copy/paste: from eco.xoptics.foo import Bar; Bar('SAROP21-...', name='bar')
```

Find it again later with `namespace.failed_items_exception["bar"]`.
