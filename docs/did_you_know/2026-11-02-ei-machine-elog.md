---
added: 2026-11-02
tags: [help, logbook, machine]
details: ask_eco
---

# Ask what happened to the machine since yesterday

`%ei` also reads the machine's elog (SwissFEL commissioning and
Diagnostics on elog-gfa.psi.ch). A question that names a time span ("last
day", "since Monday", "last night") gets that span's logbook entries in order,
and the answer goes through them with times, each linked to its entry.

```python
%ei what happened to the machine over the last day or so?
%ei any Athos problems since Friday?
```

More: {doc}`../ask_eco`
