#!/usr/bin/env python
# Back-compat alias for eco/startup_inline.py, the one real startup script
# (this file's own content used to differ -- see git history -- and
# eco_cli.py, desktop_app.py and docs/installation.md already assumed
# startup_inline.py's namespace-based semantics, not this file's old
# `{scope}.init()` call, which no longer exists on eco.bernina). Kept only
# because several /sf/bernina/bin wrapper scripts (eco, eco_new, eco-in,
# eco_from) hardcode this filename by absolute path and aren't editable
# here (root-owned).
#
# Used to exec a second copy of the script that lived at this same
# directory level (a root-level startup_inline.py, deleted now) instead of
# eco/startup_inline.py -- the two had drifted apart (confirmed 2026-09-12:
# the root copy was silently missing the kernel_registry.install_shell_
# logger() call the package copy had gained, meaning every session started
# through these /sf/bernina/bin wrappers was never actually logged despite
# eco.logs' viewer assuming it would be). Pointing this shim straight at
# eco/startup_inline.py leaves exactly one real copy to maintain, so this
# class of drift can't recur.
#
# exec'd (not run_path'd) so this executes in the caller's own namespace,
# exactly as if eco/startup_inline.py had been run directly -- matching how
# IPython's `run <path>` normally populates the interactive namespace.
from pathlib import Path

exec(
    compile(
        (Path(__file__).parent / "eco" / "startup_inline.py").read_text(),
        "eco/startup_inline.py",
        "exec",
    )
)
