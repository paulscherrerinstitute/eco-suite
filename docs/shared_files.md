# Shared files: group permissions on results and configuration

Several accounts write to the same trees: the shared `gac-bernina` account and
personal ones. That applies to the results under `/sf/<instrument>/data/<pgroup>/res`
(run data, run tables, scan info, presets, ...) and to the shared configuration
under `/sf/<instrument>/code/<account>/`. Everyone in the group must be able to
add to and rewrite these, which needs directories at `2775` (group-writable
plus setgid) and files at `664`. Python's defaults (`mkdir` → `755`,
`open(..., "w")` → `644`) silently produce files nobody else can change.
Typically you only notice when the next person can't store a run.

eco now creates everything it writes with the right mode and group, and warns
(without failing) when it meets something wrong. The warning names the
**account that owns** the offending path, since only that account (or root)
can fix it, and gives the `chgrp`/`chmod` command.

## Finding out what is wrong

```python
from eco.utilities import datafiles

datafiles.check_group_writable("/sf/bernina/data/p23415/res/run_data")
```

This lists every level that is not group-writable or has the wrong group,
together with its owner. It changes nothing.

## Repairing a tree

Run this **as the account that owns the tree**. A personal account cannot
repair `gac-bernina`'s directories.

```python
datafiles.repair_tree("/sf/bernina/code/gac-bernina/eco_cnf_bernina/memory")
```

It fixes everything this account can fix below the path, and lists the owner
of everything it cannot.

`replace_unowned=True` goes further: it replaces entries owned by *another*
account with identical copies owned by this one. That changes ownership and
cannot be undone without root, so try `dry_run=True` first. `dry_run` only
affects that ownership step; the ordinary chmod/chgrp fixes are still applied.

## Which group?

Inside a pgroup tree, the pgroup. Elsewhere, the group of the enclosing
directory (what setgid would have given). Only groups this account belongs to
can be set.

## For code that writes files

Use these instead of the plain calls, rather than adding a `chmod` by hand:

| Instead of | Use |
|------------|-----|
| `Path(p).mkdir(parents=True)` | `datafiles.ensure_dir(p)`: applies `2775` to **every** level it creates |
| `open(p, "w")` | `datafiles.open_group_writable(p, "w")` |
| a file written by another library (`to_pickle`, `shutil.copyfile`, ...) | `datafiles.ensure_group_writable(p)` afterwards |

Never `chmod(0o775)` a directory: that clears the setgid bit, after which new
files get the creator's primary group instead of the pgroup.

If an existing file is not writable by this session but its directory is,
`open_group_writable` replaces the file (write to a temporary file, then
rename) instead of failing, so a second account can still store a value. This
is reported once, since it changes the file's owner.
