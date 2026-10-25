---
added: 2026-09-12
tags: [shell, history, productivity]
details: command_history
---

# Type `...` and Tab to pick a device instead of typing its path

```text
In [1]: (1 / ...<TAB>    # recent, most used, namespace
In [1]: ...h<TAB>        # your previous commands
```

The pick is inserted at the cursor and nothing runs until you press Enter. A
`...` still in the line at Enter opens a picker window, and the expanded line
is pre-filled at the next prompt for you to check.

More: {doc}`../command_history`
