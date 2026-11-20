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

import ast
import contextlib
import getpass
import logging
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_replay_state = threading.local()


@contextlib.contextmanager
def replaying():
    """While a patch file is being replayed. `record_interactive_patch` records
    nothing inside it: a replayed entry still says `add_patch=True`, and the
    "current input" it would capture is whatever cell triggered the replay
    (e.g. `runs[12]`), so recording there would duplicate entries."""
    depth = getattr(_replay_state, "depth", 0)
    _replay_state.depth = depth + 1
    try:
        yield
    finally:
        _replay_state.depth = depth


def is_replaying():
    return getattr(_replay_state, "depth", 0) > 0

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


def run_patch_file(path, report=print, globals=None):
    """Execute every entry of the patch file at `path`; an entry that raises
    (including a `SyntaxError`) is reported through `report` and skipped, the
    rest still run. Returns `(applied, failed)` as lists of entry titles.

    Entries share one globals dict (`__name__ == "patches"`). Each is compiled
    against `path` with its real line numbers, so a traceback points at the
    right line of the file. `globals` seeds that dict (e.g. ``{"runs":
    runs}`` for a file that is not run as part of a namespace import).
    Nothing is recorded while it runs (`replaying`).
    """
    path = Path(path)
    namespace = {"__name__": "patches", "__file__": str(path)}
    namespace.update(globals or {})
    applied, failed = [], []
    for title, first_line, source in split_patch_file(path.read_text()):
        try:
            code = compile("\n" * (first_line - 1) + source, str(path), "exec")
            with replaying():
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


def record_interactive_patch(
    patch_file,
    target,
    code=None,
    root_module=None,
    known_names=(),
    replay=True,
    preamble=None,
    drop_bindings=(),
    epilogue=(),
):
    """Append an entry for `target` to `patch_file`: the IPython input that
    created it plus what that input needs to run again in a fresh session
    (imports, and with `replay=True` the earlier cells that defined the
    session-only names it uses -- see
    `eco.elements.assembly._patch_prelude`).

    `code` is the statement to record; None records the cell that is being
    executed. An explicit `code` (e.g. a clean ``runs.add_append_callback(
    "x", foo)``) is analysed as if typed after the current cell, so the
    current cell can supply its definitions.

    `known_names` are names the file provides itself (they are neither
    imported nor replayed); with `root_module`, a new file starts with
    ``from <root_module> import *`` and that module's names count as known.
    `preamble` is a comment written at the top of a new file.

    `drop_bindings`: names that must *not* be re-created on replay (e.g. an
    example run, `ds = runs[3]`, whose role the replayed code gives to
    another object). Top-level plain assignments to them are removed from the
    recorded code and replayed cells; a replayed cell that still uses them
    outside a function body is reported, since it will fail on replay.
    `epilogue` lines are written after the code.

    Best effort: returns True if written; a failure only logs a warning.
    Does nothing while a patch file is being replayed (`replaying`).
    """
    import sys

    if is_replaying():
        return False
    from ..elements.assembly import _capture_current_input, _patch_prelude
    from .datafiles import ensure_dir, ensure_group_writable

    patch_file = Path(patch_file)
    current = _capture_current_input()
    if code is None:
        code = current
    if not code:
        logger.warning(
            "patch for '%s': could not capture the generating IPython input "
            "(not running interactively?) -- nothing saved.",
            target,
        )
        return False
    imports, cells, notes = [], [], []
    try:
        from IPython import get_ipython

        ip = get_ipython()
        transform = ip.input_transformer_manager.transform_cell
        known = set(known_names)
        if root_module and root_module in sys.modules:
            known |= set(vars(sys.modules[root_module]).keys())
        history = list(ip.history_manager.input_hist_raw)
        if code is not current:
            # analyse the explicit statement as the newest input, so the
            # cell being executed is searched for definitions as well
            history.append(code)
        imports, cells, notes = _patch_prelude(
            code,
            ip.user_ns,
            history,
            known | set(drop_bindings),
            replay=replay,
            transform=transform,
        )
        # magics come out as `get_ipython().run_line_magic(...)`
        code = transform(code)
        if "get_ipython" in code + "".join(c for _, c in cells):
            imports.append("from IPython import get_ipython")
    except Exception:
        logger.debug("patch import analysis failed", exc_info=True)
    if drop_bindings:
        drop = set(drop_bindings)
        code = drop_assignments(code, drop)
        cells = [(i, drop_assignments(c, drop)) for i, c in cells]
        for i, c in cells:
            used = sorted(names_used_outside_functions(c) & drop)
            if used:
                logger.warning(
                    "patch for '%s': In[%d] uses %s outside a function; it "
                    "will fail when the patch is replayed.",
                    target, i, ", ".join(used),
                )
    for i, c in cells:
        logger.warning(
            "patch for '%s': also recording In[%d] (%s), which it was built "
            "from; it re-runs when the patch is replayed.",
            target,
            i,
            c.splitlines()[0][:60] if c else "",
        )
    for n in notes:
        logger.warning("patch for '%s': %s", target, n.lstrip("# "))
    try:
        ensure_dir(patch_file.parent)
        is_new = not patch_file.exists()
        with open(patch_file, "a") as f:
            if is_new:
                if preamble:
                    f.write(preamble.rstrip("\n") + "\n")
                if root_module:
                    f.write(f"from {root_module} import *\n")
            f.write(
                "\n"
                + format_entry_header(
                    target,
                    datetime.now().isoformat(timespec="seconds"),
                    getpass.getuser(),
                )
                + "\n"
            )
            for line in imports + notes:
                f.write(line + "\n")
            for i, c in cells:
                f.write(f"# --- In[{i}], replayed: origin of an object used below ---\n")
                f.write(c + "\n")
            f.write(code.rstrip("\n") + "\n")
            for line in epilogue:
                f.write(line + "\n")
        ensure_group_writable(patch_file)
    except Exception:
        logger.warning(
            "patch for '%s': failed to write to %s", target, patch_file, exc_info=True
        )
        return False
    return True


def drop_assignments(source, names):
    """`source` without its top-level statements that only assign to `names`
    (``ds = runs[3]``, ``a = b = ...`` with all targets in `names`). Each is
    replaced by a comment, keeping the rest of the text as typed; if such a
    statement shares a line with another one, the code is re-generated
    (`ast.unparse`, comments lost)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    drop = []
    for st in tree.body:
        if isinstance(st, ast.Assign):
            targets = st.targets
        elif isinstance(st, ast.AnnAssign) and st.value is not None:
            targets = [st.target]
        else:
            continue
        if all(isinstance(t, ast.Name) and t.id in names for t in targets):
            drop.append(st)
    if not drop:
        return source
    dropped_lines = set()
    for st in drop:
        dropped_lines.update(range(st.lineno, st.end_lineno + 1))
    shared = any(
        st not in drop
        and dropped_lines & set(range(st.lineno, st.end_lineno + 1))
        for st in tree.body
    )
    if shared:
        tree.body = [st for st in tree.body if st not in drop] or [ast.Pass()]
        return ast.unparse(tree)
    lines = source.splitlines()
    out = []
    for n, line in enumerate(lines, start=1):
        if n not in dropped_lines:
            out.append(line)
        elif any(st.lineno == n for st in drop):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}# (not replayed: {line.strip()} -- see drop_bindings)")
    return "\n".join(out)


def names_used_outside_functions(source):
    """Names read by `source` outside function/lambda bodies, i.e. when it is
    executed rather than when something it defines is called."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    used = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # defaults and decorators are evaluated at definition time
            stack.extend(d for d in node.args.defaults + node.args.kw_defaults if d is not None)
            stack.extend(getattr(node, "decorator_list", []))
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        stack.extend(n for n in ast.iter_child_nodes(node) if n is not None)
    return used
