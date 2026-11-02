---
added: 2026-11-02
tags: [help, history]
details: ask_eco
---

# Jump from an answer to where it came from

Every `%ei` answer ends with its numbered sources, each with its
location: file and line of a command or doc section, the elog entry, the
SciLog logbook. In gnome-terminal they are links (Ctrl+click).
`%ei --open N` shows source N in its context: the commands typed before and
after it with what came back, or a logbook post between its neighbours.

```python
%ei how did we align the mono last time?
%ei --open 2
```

More: {doc}`../ask_eco`
