"""Reading and running the per-pgroup patch file that
`Assembly._append(..., add_patch=True)` writes (see
`eco.elements.assembly.Assembly._write_patch`).

The file is a sequence of *entries*, each introduced by a header line
(`format_entry_header`). A plain `import patches` runs it as one module, so
the first failing statement -- a syntax error, a device that is gone, a
replayed cell that raises -- silently cost every entry after it as well.
`run_patch_file` runs the entries one by one instead, in one shared set of
globals (so an entry can use what an earlier one defined, exactly as in the
module), and reports each failure separately.
"""

import re
import traceback
from pathlib import Path

_HEADER_RE = re.compile(r"^# --- patch for (\S+) \(.*\) ---\s*$")


def format_entry_header(target, timestamp, user):
    """The line that starts a patch entry; `run_patch_file` splits on it."""
    return f"# --- patch for {target} ({timestamp}, {user}) ---"


def split_patch_file(text):
    """[(title, first_line_number, source)] -- the text before the first
    header (the `from <namespace> import *` preamble, a hand-written
    section) as title "preamble", then one item per header. Line numbers are
    1-based positions in the file."""
    lines = text.splitlines(keepends=True)
    starts = [
        (i, m.group(1))
        for i, ln in enumerate(lines)
        if (m := _HEADER_RE.match(ln.rstrip("\n")))
    ]
    sections = []
    first = starts[0][0] if starts else len(lines)
    if any(ln.strip() for ln in lines[:first]):
        sections.append(("preamble", 1, "".join(lines[:first])))
    for k, (i, target) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        sections.append((target, i + 1, "".join(lines[i:end])))
    return sections


def run_patch_file(path, report=print):
    """Execute every entry of the patch file at `path`; an entry that raises
    (including a `SyntaxError`) is reported through `report` and skipped, the
    rest still run. Returns `(applied, failed)` as lists of entry titles.

    Entries share one globals dict (`__name__ == "patches"`). Each is compiled
    against `path` with its real line numbers, so a traceback points at the
    right line of the file.
    """
    path = Path(path)
    namespace = {"__name__": "patches", "__file__": str(path)}
    applied, failed = [], []
    for title, first_line, source in split_patch_file(path.read_text()):
        try:
            code = compile("\n" * (first_line - 1) + source, str(path), "exec")
            exec(code, namespace)
        except Exception as e:
            failed.append(title)
            # drop this function's own `exec(...)` frame: the first frame
            # shown is then the offending line of the patch file.
            tb = "".join(
                traceback.format_exception(type(e), e, e.__traceback__.tb_next)
            )
            report(
                f"eco: patch '{title}' in {path} (starting at line "
                f"{first_line}) failed and was skipped:\n"
                f"{tb}"
            )
        else:
            applied.append(title)
    if failed:
        report(
            f"eco: {path}: {len(applied)} section(s) applied, "
            f"{len(failed)} failed: {', '.join(failed)}"
        )
    return applied, failed
