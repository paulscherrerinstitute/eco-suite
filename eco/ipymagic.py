"""Opt-in inline component picker for IPython: write a bare ``...``
(the builtin ``Ellipsis`` literal) anywhere an expression is expected, and
it's replaced -- before execution -- by whatever component you pick from
the namespace tree. The *expanded* code (with the picked component's real
dotted path spliced in, not a placeholder) is printed first, then run --
so what actually executed is always visible and re-typeable::

    from eco import bernina
    from eco import ipymagic
    ipymagic.start(bernina)

    In [1]: (1 / ...).plot()
    (1 / bernina.mono.energy).plot()
    <plot appears>

    In [2]: cen = (left := ...) - (right := ...)
    cen = (left := bernina.mono.energy) - (right := bernina.attenuator.transmission)

Each ``...`` prompts once, in left-to-right order, and the picker itself
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

Deliberately namespace-tree-only for now -- see the module TODO below for
picking from prior *commands* (eco.logs' kernel history) as a planned
second mode, likely via a different trigger so the two don't collide.
"""
import ast

_state = {}


def _is_marker(node):
    return isinstance(node, ast.Constant) and node.value is Ellipsis


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
        from eco.widgets.component_selector_qt import pick_component_modal

        path, obj = pick_component_modal(
            self.root,
            kind_filter=self.kind_filter,
            bookmarks=self.bookmarks,
            recent=self.recent,
        )
        if path is None:
            print("eco.ipymagic: selection cancelled -- left `...` in place")
            return node
        self.picked_any = True
        replacement = ast.parse(path, mode="eval").body
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
            print(ast.unparse(node))
        return node


_ast_transformer = _AstTransformer()


def start(root, kind_filter="All", bookmarks=None, recent=None):
    """Register the `...` picker on the *current* IPython shell.

    `root`: the namespace/Assembly tree to pick from (e.g. `bernina`).
    `kind_filter`/`bookmarks`/`recent`: passed straight through to the
    picker window each time (see `eco.widgets.component_selector_qt.
    pick_component_modal`) -- left as `None` picks up that frontend's own
    per-user, per-namespace defaults, same as opening it any other way.

    Calling this again just repoints `root` (etc.) for the next pick --
    it does not install a second hook, so re-running a startup cell is
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
        ip._eco_ipymagic_installed = True


def stop():
    """Turn picking off (`...` goes back to being a plain literal) without
    needing a shell restart -- e.g. a notebook cell that wants to type a
    real Ellipsis afterward."""
    _state["root"] = None
