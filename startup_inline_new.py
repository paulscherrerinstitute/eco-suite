#!/usr/bin/env python
# Back-compat alias for startup_inline.py, which is now the one real
# startup script (this file's content used to differ -- see git history --
# and eco_cli.py, desktop_app.py and docs/installation.md already assumed
# startup_inline.py's namespace-based semantics, not this file's old
# `{scope}.init()` call, which no longer exists on eco.bernina). Kept only
# because several /sf/bernina/bin wrapper scripts (eco, eco_new, eco-in,
# eco_from) hardcode this filename by absolute path and aren't editable
# here (root-owned) -- point any new caller at startup_inline.py directly.
#
# exec'd (not run_path'd) so this executes in the caller's own namespace,
# exactly as if startup_inline.py had been run directly -- matching how
# IPython's `run <path>` normally populates the interactive namespace.
from pathlib import Path

exec(
    compile(
        Path(__file__).with_name("startup_inline.py").read_text(),
        "startup_inline.py",
        "exec",
    )
)
