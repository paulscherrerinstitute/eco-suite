---
added: 2026-10-30
tags: [help, logbook]
details: ask_eco
---

# Photograph the shift notes, and %ei finds them later

With a model connected, `%ei --scan` reads photos, scanned PDFs and
whiteboard pictures once, handwriting included, and adds the text to what
`%ei` searches, dated by the date written on the note. `%ei --webcam` takes
the picture for you, and `%ei --scan-image img` reads a camera frame from the
session.

```python
%ei --scan ~/photos/shift_2026-10-14/
%ei what did we write about the tapedrive trigger?
```

More: {doc}`../ask_eco`
