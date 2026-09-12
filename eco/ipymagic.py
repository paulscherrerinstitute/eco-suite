"""Opt-in inline component picker for IPython: write a bare ``...``
(the builtin ``Ellipsis`` literal) anywhere an expression is expected, and
it's replaced -- before execution -- by whatever you pick in a small Qt
window with two tabs, Components (the namespace tree) and Commands (prior
input, from `eco.logs`). The *expanded* code (with the picked component's
real dotted path, or the picked command's raw text, spliced in) never runs
on its own -- it's handed straight to the next prompt/cell, pre-filled and
ready to edit, exactly like ``%edit``/``%load`` do, so only something you
explicitly submit yourself ever actually executes::

    from eco import bernina
    from eco import ipymagic
    ipymagic.start(bernina)

    In [1]: (1 / ...).plot()
    <pick "mono.energy" in the Components tab -- Enter, double-click, or Go>
    In [2]: (1 / bernina.mono.energy).plot()
    <cursor sits here, ready to edit or just press Enter>

    In [2]: cen = (left := ...) - (right := ...)
    In [3]: cen = (left := bernina.mono.energy) - (right := bernina.attenuator.transmission)

Each ``...`` prompts once, in left-to-right order. In the picker, a single
click only *highlights* a row (in either tab, or a Recent/Bookmark choice)
-- it never picks anything by itself, so idle browsing is safe. Only
pressing Enter/Return, double-clicking a row, or the one shared Go button
below both tabs actually commits a pick. The picker itself
(``eco.widgets.component_selector_qt``) remembers its own fold/search state
and a per-user Recent list across invocations already -- nothing extra
needed here for that.

Why ``...`` and not a named sentinel object: it's already a real Python
literal (the builtin ``Ellipsis`` singleton -- ``arr[...]``, type-stub
bodies, "fill this in" generally), not an IPython special form, so the
interception happens the same way anywhere IPython's own
``ast_transformers`` hook runs -- a terminal session, a Jupyter notebook/
JupyterLab, qtconsole -- with nothing IPython-specific about the trigger
itself, only about how it's intercepted. No name to import, no risk of
shadowing a real variable, nothing to rename if `ipymagic` itself gets
renamed later.

A bare ``...`` used as a numpy/array subscript (``arr[...]``) is left
alone -- only a ``...`` used as its own expression is treated as "pick
something here". Closing the picker without choosing anything leaves the
literal ``...`` in place, so it fails exactly the way typing a real
``Ellipsis`` there would (a clear `TypeError` if it's actually used, no
silent auto-pick).

Terminal overlay (Tab-completion mode)
--------------------------------------
In a real terminal (or a notebook -- ipykernel's completion goes through
the same IPCompleter matcher pipeline), typing the trigger and pressing
Tab pops IPython's own native completion menu -- the same widget/rendering
Python attribute completion already uses -- populated with three sections
instead of attributes: Recent, Recommended (ranked by actual use-count via
`eco.elements.recent.FrequencyCounts`, not just recency), and (component
mode only) a live, filterable walk of the whole namespace tree. This is
the preferred way to pick something in a terminal: it never blocks on a
Qt window, and -- critically -- it only ever *inserts text at the cursor*,
never executes anything, so you can keep composing/editing the line and
submit whenever you're ready yourself:

    In [1]: (1 / ...<TAB>
            .bernina.mono.energy        recent
            .bernina.attenuator.transmission   recommended
            .bernina.slit1.width        namespace
    In [1]: (1 / bernina.mono.energy

Typing more after the trigger (`...ene<TAB>`) narrows the namespace
section by substring match; Recent/Recommended stay unfiltered (picking
something you use constantly shouldn't require spelling it first).

A second trigger, `...h` (mnemonic: history), does the same for prior
*commands* (`eco.logs.recent_commands`/`frequent_commands`) instead of
components -- no namespace section (commands have no tree to browse), and
picking one always just inserts its text for further editing, the same
"never auto-executes" rule as components. Both share one on/off switch
(`start()`/`stop()`); there's no separate flag for the completion path
since it always inserts rather than executes, so there's nothing about it
that needs its own opt-out.

If you never press Tab and just hit Enter with a literal `...` still in
the line, the `...`-in-an-expression mechanism above (the Qt modal picker)
still applies as a fallback -- terminal session included -- since the two
compose rather than conflict: accepting a Tab-completion replaces the
`...` in the buffer before the line is ever submitted, leaving nothing for
the AST transform to find, so it only ever fires when Tab wasn't used.
The one case it backs off in is no display being available at all (no
`DISPLAY`/`WAYLAND_DISPLAY`, e.g. a plain SSH session with no X
forwarding) -- opening a `QApplication` there doesn't raise a catchable
exception, it aborts the whole process, so that case is checked for and
turned into a one-line printed hint pointing at Tab instead, `...` left in
place, rather than attempted.
"""
import ast

from IPython.core.completer import SimpleCompletion, context_matcher

COMPONENT_TRIGGER = "..."
COMMAND_TRIGGER = "...h"
_RECENT_COUNT = 8
_RECOMMENDED_COUNT = 8
_NAMESPACE_LIMIT = 30

_state = {}


def _is_marker(node):
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _full_path(root, relative_path):
    """`component_selector.build_tree`/`resolve_path` paths (and so
    `pick_component_modal`'s return value) are relative to `root` --
    plain `getattr` chains starting *from* root, never including root's
    own name (confirmed against `Alias.get_full_name(base=...)`, which
    stops at `base` and returns "" for `base` itself) -- not a standalone
    expression that means anything on its own. This combines it with the
    name `root` is actually bound to in the executing namespace (its own
    `.name`, matching this codebase's convention that a namespace object's
    `.name` already matches how it's imported at the top level -- e.g.
    `import eco.bernina as bernina` gives an object whose `.name` is
    already "bernina", and `eco.ipymagic.start()`'s own docstring assumes
    exactly this) so the printed/inserted code is directly executable and
    re-typeable on its own, not silently relative to some invisible root.

    Contrast with `eco.elements.recent.RecentComponents`/`FrequencyCounts`
    paths, which come from `Alias.get_full_name()` with *no* base -- those
    are already absolute and must NOT be run through this again.
    """
    root_name = getattr(root, "name", None) or "root"
    if not relative_path:
        return root_name
    return f"{root_name}.{relative_path}"


def _has_display():
    """Whether opening a Qt window is actually possible right now. This
    exists because creating a `QApplication` with no display connected
    doesn't raise a catchable Python exception -- it's a native abort
    (confirmed directly: `QApplication([])` with `DISPLAY` unset kills the
    whole interpreter with SIGABRT, no `except` clause runs) -- so this
    must be checked *before* ever constructing one, not caught around it.

    A `QApplication` that already exists (e.g. `%gui qt` already enabled,
    or an earlier picker) means Qt is already known-safe regardless of the
    checks below. Otherwise this is the same `DISPLAY`/`WAYLAND_DISPLAY`
    convention X11/Wayland clients generally rely on; macOS/Windows have no
    such env var and are assumed fine (a real deploy target for eco is
    Linux/X11 only, so this is best-effort for the rest)."""
    import os
    import sys

    from qtpy import QtWidgets

    if QtWidgets.QApplication.instance() is not None:
        return True
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class _EllipsisPicker(ast.NodeTransformer):
    """Replaces every bare (non-subscript-slice) ``...`` in a parsed AST
    with the dotted-path expression of whatever the user picks, depth/
    left-to-right in source order (so `(left := ...) - (right := ...)`
    prompts for `left` before `right`). Leaves a cancelled pick's literal
    `...` untouched."""

    def __init__(self, root, kind_filter, bookmarks, recent):
        self.root = root
        self.kind_filter = kind_filter
        self.bookmarks = bookmarks
        self.recent = recent
        self.picked_any = False

    def visit_Subscript(self, node):
        # descend into everything except the slice itself, so `arr[...]`
        # is left alone -- only node.value (e.g. a pick used as part of
        # what's being subscripted, `(...).plot()[0]`) is still eligible.
        node.value = self.visit(node.value)
        return node

    def visit_Constant(self, node):
        if not _is_marker(node):
            return node
        if not _has_display():
            print(
                "eco.ipymagic: '...' reached Enter unexpanded -- no display available to open "
                "the component picker window (DISPLAY/WAYLAND_DISPLAY not set). Type '...' "
                "(or '...h' for a command) then press Tab to pick inline instead. Left '...' "
                "in place."
            )
            return node
        from eco.widgets.component_selector_qt import pick_component_modal

        kind, text, obj = pick_component_modal(
            self.root,
            kind_filter=self.kind_filter,
            bookmarks=self.bookmarks,
            recent=self.recent,
        )
        if kind is None:
            print("eco.ipymagic: selection cancelled -- left `...` in place")
            return node
        self.picked_any = True
        # A "command" pick is already a full, standalone expression string
        # (e.g. "mono.mv(5)") -- unlike a "component" pick (a bare dotted
        # path *relative to* root, see _full_path's docstring), it must
        # NOT be run through _full_path.
        expression = _full_path(self.root, text) if kind == "component" else text
        replacement = ast.parse(expression, mode="eval").body
        return ast.copy_location(replacement, node)


class _AstTransformer:
    """Registered directly into IPython's `ast_transformers` list.
    `InteractiveShell.transform_ast` calls `.visit(node)` on each entry
    itself -- it expects an `ast.NodeTransformer`-shaped object, not a
    plain function (confirmed the hard way: a bare function registered
    there gets one `AttributeError: 'function' object has no attribute
    'visit'`, then IPython silently unregisters it and moves on -- no
    exception surfaces to the caller, `...` just quietly stops being
    intercepted)."""

    def visit(self, node):
        if _state.get("root") is None:
            return node
        picker = _EllipsisPicker(
            _state["root"], _state["kind_filter"], _state["bookmarks"], _state["recent"]
        )
        node = picker.visit(node)
        if picker.picked_any:
            expanded = ast.unparse(node)
            from IPython import get_ipython

            ip = get_ipython()
            if ip is not None:
                # Cancel *this* cell's execution (an empty body -- nothing
                # left to run) and hand the expanded code to the next
                # prompt/cell instead, pre-filled and ready to edit --
                # `set_next_input` is the same mechanism `%edit`/`%load`
                # use, and (unlike printing-then-executing, this file's
                # original approach) it works in both a terminal (fills
                # the next `In [n]:` line, see TerminalInteractiveShell.
                # prompt_for_code's `rl_next_input` check) and a notebook/
                # qtconsole kernel (ZMQInteractiveShell overrides
                # set_next_input to send a payload the frontend turns into
                # a new, pre-filled cell) -- so what actually runs is
                # always something the user explicitly submitted
                # themselves, never something this module ran on its own.
                ip.set_next_input(expanded, replace=True)
                node.body = []
            else:
                # No live IPython session to hand a next-input to (should
                # not happen in practice -- this transformer only ever
                # runs as part of IPython's own cell-execution pipeline --
                # kept as a safe fallback rather than silently dropping
                # the expansion).
                print(expanded)
        return node


_ast_transformer = _AstTransformer()


def _flatten_paths(node, out=None):
    """All dotted paths in a `component_selector.ComponentNode` tree,
    depth-first (the root itself included, if it has a name)."""
    if out is None:
        out = []
    if node.name:
        out.append(node.name)
    for child in node.children:
        _flatten_paths(child, out)
    return out


def _ranked_completions(recent_items, recommended_items, extra_items, query):
    """Recent, then Recommended (recommended items already in Recent are
    dropped -- no point suggesting the same thing twice), then whatever
    `extra_items` are left (already excludes both, and already filtered
    by `query` by the caller -- Recent/Recommended stay unfiltered)."""
    completions = [SimpleCompletion(text=p, type="recent") for p in recent_items]
    recommended_items = [p for p in recommended_items if p not in recent_items]
    completions += [SimpleCompletion(text=p, type="recommended") for p in recommended_items]
    completions += [SimpleCompletion(text=p, type="namespace") for p in extra_items]
    return completions


@context_matcher()
def _component_matcher(context):
    token = context.token
    root = _state.get("root")
    if root is None or not token.startswith(COMPONENT_TRIGGER) or token.startswith(
        COMMAND_TRIGGER
    ):
        return {"completions": []}

    from eco.elements.recent import FrequencyCounts, RecentComponents
    from eco.widgets.component_selector import build_tree

    namespace_name = getattr(root, "name", None)
    recent_paths = RecentComponents(namespace_name=namespace_name).all()[:_RECENT_COUNT]
    recommended_paths = FrequencyCounts(namespace_name=namespace_name).top(
        _RECOMMENDED_COUNT * 2
    )[:_RECOMMENDED_COUNT]

    query = token[len(COMPONENT_TRIGGER) :].lower()
    exclude = set(recent_paths) | set(recommended_paths)
    # build_tree's paths are relative to root (see _full_path's docstring)
    # -- convert to absolute *before* dedup/query matching, so they compare
    # correctly against Recent/Recommended's already-absolute paths.
    namespace_paths = [
        full
        for full in (_full_path(root, p) for p in _flatten_paths(build_tree(root)))
        if full not in exclude and (not query or query in full.lower())
    ][:_NAMESPACE_LIMIT]

    completions = _ranked_completions(recent_paths, recommended_paths, namespace_paths, query)
    return {"completions": completions, "suppress": True}


@context_matcher()
def _command_matcher(context):
    token = context.token
    if _state.get("root") is None or not token.startswith(COMMAND_TRIGGER):
        return {"completions": []}

    from eco import logs

    recent_cmds = logs.recent_commands(n=_RECENT_COUNT)
    recommended_cmds = logs.frequent_commands(n=_RECOMMENDED_COUNT * 2)[:_RECOMMENDED_COUNT]

    query = token[len(COMMAND_TRIGGER) :].lower()

    def _keep(cmd):
        return not query or query in cmd.lower()

    recent_cmds = [c for c in recent_cmds if _keep(c)]
    recommended_cmds = [c for c in recommended_cmds if _keep(c)]

    completions = _ranked_completions(recent_cmds, recommended_cmds, [], query)
    return {"completions": completions, "suppress": True}


def start(root, kind_filter="All", bookmarks=None, recent=None):
    """Register the `...` picker on the *current* IPython shell: both the
    Tab-completion overlay (`...`/`...h`, terminal and notebook alike) and
    the Qt-modal fallback for a `...` that reaches Enter unexpanded.

    `root`: the namespace/Assembly tree to pick from (e.g. `bernina`).
    `kind_filter`/`bookmarks`/`recent`: passed straight through to the
    Qt-modal picker each time it opens (see `eco.widgets.
    component_selector_qt.pick_component_modal`) -- left as `None` picks up
    that frontend's own per-user, per-namespace defaults, same as opening
    it any other way. The Tab-completion overlay always uses its own
    per-namespace Recent/Recommended (see `eco.elements.recent`), not
    these -- there's no window to hand a shared instance to.

    Calling this again just repoints `root` (etc.) for the next pick --
    it does not install the hooks twice, so re-running a startup cell is
    safe.
    """
    from IPython import get_ipython

    ip = get_ipython()
    if ip is None:
        raise RuntimeError("eco.ipymagic.start() needs a running IPython session")

    _state["root"] = root
    _state["kind_filter"] = kind_filter
    _state["bookmarks"] = bookmarks
    _state["recent"] = recent

    if not getattr(ip, "_eco_ipymagic_installed", False):
        ip.ast_transformers.append(_ast_transformer)
        ip.Completer.custom_matchers.extend([_component_matcher, _command_matcher])
        ip._eco_ipymagic_installed = True


def stop():
    """Turn picking off (`...`/`...h` go back to being plain text/a plain
    literal) without needing a shell restart -- e.g. a notebook cell that
    wants to type a real Ellipsis afterward. The completion matchers stay
    registered (harmless: both already no-op whenever `_state["root"]` is
    `None`, checked first) -- only `_state["root"]` is cleared, same as
    the Qt-modal fallback's own gate."""
    _state["root"] = None
