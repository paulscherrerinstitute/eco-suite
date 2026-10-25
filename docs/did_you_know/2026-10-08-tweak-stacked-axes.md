---
added: 2026-10-08
tags: [tweak, alignment]
details: tweak
---

# Tweak up to four axes at once, one keyboard row each

```python
from eco.elements.adjustable import Tweak
Tweak((m1, 0.1), (m2, 0.1), (m3, 0.05)).tweak()
```

Each axis gets a row: `j k l ;`, `u i o p`, `7 8 9 0` (negative, step×2,
step/2, positive). A fourth axis adds `m , . /`.

More: {doc}`../tweak`
