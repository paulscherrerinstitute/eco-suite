---
added: 2026-09-03
tags: [shell, namespace]
details: tab_completion
---

# Tab never initializes a device behind your back

Completing into a lazy device that isn't initialized yet now asks first:

```text
In [1]: prepump.<TAB>
eco: 'prepump' is not initialized yet -- press Tab again to initialize it and complete.
```

Press Tab again to build it and complete. Only that one component is touched,
never its siblings, and completion several levels deep
(`prepump.line1_usd.<TAB>`) works too.

More: {doc}`../tab_completion`
