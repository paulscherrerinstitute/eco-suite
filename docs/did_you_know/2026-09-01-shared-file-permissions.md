---
added: 2026-09-01
tags: [data, permissions]
details: shared_files
---

# "Why can't my colleague write here?" now has an answer

eco creates run data and shared config group-writable, and warns, naming the
owning account, when it finds something that isn't. To check or fix a tree:

```python
from eco.utilities import datafiles
datafiles.check_group_writable("/sf/bernina/data/p23415/res/run_data")   # lists problems, changes nothing
datafiles.repair_tree(path)                                              # run as the owning account
```

More: {doc}`../shared_files`
