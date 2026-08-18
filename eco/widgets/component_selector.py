"""Backend-agnostic model for a graphical "component selector": browse an
eco namespace/Assembly tree and pick a component out of it -- typically an
Adjustable or a Detector, but any node can be selected.

Two ready-made frontends consume this model and share its contract
(`get_selected()` / `on_select(cb)`), so either can be dropped into a larger
UI as a sub-component:
    - eco.widgets.component_selector_widget  (ipywidgets, for notebooks)
    - eco.widgets.component_selector_qt      (Qt, via qtpy, for scripts/GUIs)

`select_component()` below picks whichever of the two is appropriate, the
same way `Assembly.widget()` already does for the display widgets.

Lazy namespace items (see eco.utilities.config.Namespace: a not-yet-built
top-level component is a `lazy_object_proxy.Proxy` that constructs the real
device -- real EPICS connections included -- the instant ANY attribute is
touched on it, even `isinstance()`/`hasattr()`) are walked *by name only*:
`build_tree()` never dereferences one. They show up as a distinct "lazy"
kind so the tree can be browsed in full without silently paying to
initialize every device it lists. The one place that can trigger a real
build is `resolve_path()` (a plain attribute walk from the root) - used
explicitly when a bookmark is followed or a lazy node is picked - exactly
like typing `namespace.some.path` yourself.
"""
from pathlib import Path
from typing import Any, List, NamedTuple, Optional

from eco.elements.adjustable import AdjustableFS

try:
    from eco.elements.protocols import Adjustable, Detector
    from eco.elements.assembly import FailedComponent
except Exception:  # pragma: no cover - keeps this module importable standalone
    Adjustable = ()
    Detector = ()
    FailedComponent = ()


class ComponentNode(NamedTuple):
    """One entry in a browsed namespace tree.

    `name` is the dotted path relative to the root passed to `build_tree()`
    (so it round-trips through `resolve_path(root, node.name)`). `obj` is
    the live object, or `None` for a `kind == "lazy"` node (deliberately
    never resolved by the tree walk). `kind` is one of "assembly",
    "adjustable", "detector", "lazy", "failed", "other".
    """

    name: str
    obj: Any
    kind: str
    children: List["ComponentNode"]


KIND_ICONS = {
    "adjustable": "✏️",  # pencil
    "detector": "\U0001f441️",  # eye
    "assembly": "\U0001f4c1",  # folder
    "lazy": "⏳",  # hourglass
    "failed": "⚠️",  # warning
    "other": "•",  # bullet
}


def classify(obj) -> str:
    """Classify an already-resolved (never a lazy proxy) object. Priority
    mirrors `Assembly.get_display_str()`: Adjustable/Detector wins over
    "assembly" even if the object also carries a `status_collection` of its
    own (some compound devices are both)."""
    if isinstance(obj, FailedComponent):
        return "failed"
    is_adjustable = isinstance(obj, Adjustable)
    if is_adjustable:
        return "adjustable"
    if isinstance(obj, Detector):
        return "detector"
    if hasattr(obj, "status_collection"):
        return "assembly"
    return "other"


def _is_namespace_like(obj) -> bool:
    """Duck-types eco.utilities.config.Namespace (the top-level beamline
    namespace, and in principle any nested one): has separate
    lazy/initialized/failed bookkeeping on top of the plain status_collection
    every Assembly has."""
    return hasattr(obj, "lazy_names") and hasattr(obj, "initialized_items")


def _direct_children(obj):
    """Yield (name_or_None, item_or_None, is_lazy) for obj's direct children
    without ever dereferencing a lazy one.

    - Namespace-like `obj`: initialized/failed children are concrete objects
      (yielded with their registered name); lazy children are yielded as
      (name, None, True) -- name only, never touched.
    - plain Assembly: children come from `status_collection._list`. These
      are always concrete objects -- building an Assembly eagerly builds its
      declared sub-components in `__init__`, so touching them here is safe
      (no separate lazy scheme at this level).
    """
    if _is_namespace_like(obj):
        seen = set()
        out = []
        for name in sorted(obj.initialized_names):
            item = obj.initialized_items[name]
            out.append((name, item, False))
            seen.add(id(item))
        for name in sorted(getattr(obj, "failed_names", ())):
            item = obj.failed_items[name]
            if id(item) in seen:
                continue
            out.append((name, item, False))
            seen.add(id(item))
        for name in sorted(obj.lazy_names):
            out.append((name, None, True))
        return out

    sc = getattr(obj, "status_collection", None)
    if sc is None:
        return []
    out = []
    for wref in list(sc._list):
        item = wref()
        if item is None or item is obj:
            continue
        out.append((None, item, False))
    return out


def build_tree(
    root, max_depth: int = 25, _base=None, _visited: Optional[set] = None
) -> ComponentNode:
    """Recursively walk `root`'s structure into a `ComponentNode` tree.
    `root` itself must already be a concrete object (never a lazy proxy --
    the caller obtaining it, e.g. `namespace.some_device`, already resolved
    it by attribute access). `max_depth` and an identity-based visited set
    guard against runaway/circular structures."""
    if _base is None:
        _base = root
    if _visited is None:
        _visited = set()
    _visited = _visited | {id(root)}

    try:
        own_name = root.alias.get_full_name(base=_base)
    except Exception:
        own_name = getattr(root, "name", None) or ""

    children = []
    if max_depth > 0:
        for child_name, item, is_lazy in _direct_children(root):
            if is_lazy:
                dotted = f"{own_name}.{child_name}" if own_name else child_name
                children.append(
                    ComponentNode(name=dotted, obj=None, kind="lazy", children=[])
                )
                continue
            if item is None or id(item) in _visited:
                continue
            try:
                dotted = item.alias.get_full_name(base=_base)
            except Exception:
                dotted = getattr(item, "name", None) or repr(item)
            kind = classify(item)
            has_children = hasattr(item, "status_collection") or _is_namespace_like(
                item
            )
            if has_children:
                sub = build_tree(
                    item, max_depth=max_depth - 1, _base=_base, _visited=_visited
                )
                children.append(
                    ComponentNode(name=dotted, obj=item, kind=kind, children=sub.children)
                )
            else:
                children.append(
                    ComponentNode(name=dotted, obj=item, kind=kind, children=[])
                )

    return ComponentNode(name=own_name, obj=root, kind=classify(root), children=children)


def filter_tree(node: ComponentNode, kind: str = "All", search: str = "") -> Optional[ComponentNode]:
    """Return a filtered copy of `node`'s subtree, or `None` if nothing in it
    survives. "assembly" nodes are pure structure -- kept whenever a
    descendant survives filtering, regardless of `kind`/`search` on the
    assembly's own name. "lazy" nodes have an unknown real type until built,
    so they always pass the `kind` filter (never hidden just because you
    filtered to "Adjustable"), but still respect `search`. Everything else
    (adjustable/detector/failed/other) is filtered by both.
    """
    search_l = (search or "").strip().lower()
    filtered_children = [
        fc
        for fc in (filter_tree(c, kind=kind, search=search_l) for c in node.children)
        if fc is not None
    ]

    if node.kind == "assembly":
        return node._replace(children=filtered_children) if filtered_children else None

    search_ok = (not search_l) or (search_l in node.name.lower())

    if node.kind == "lazy":
        return node._replace(children=filtered_children) if search_ok else None

    kind_ok = (not kind) or kind == "All" or node.kind == kind.lower()
    if kind_ok and search_ok:
        return node._replace(children=filtered_children)
    return None


def filter_root(root: ComponentNode, kind: str = "All", search: str = "") -> ComponentNode:
    """Like `filter_tree`, but for the top-level node of a browse session:
    the root itself is always kept (even with zero matching children) so a
    frontend can still render an (empty) container instead of nothing."""
    children = [
        fc
        for fc in (filter_tree(c, kind=kind, search=search) for c in root.children)
        if fc is not None
    ]
    return root._replace(children=children)


def resolve_path(root, dotted_path: str):
    """Resolve a dotted path (as produced in `ComponentNode.name`) back to
    the live object, relative to `root`. Plain attribute access -- so
    walking through a not-yet-built lazy namespace item builds it, same as
    typing `root.some.path` by hand. Pass `""`/`None` to mean `root` itself.
    """
    obj = root
    if dotted_path:
        for part in dotted_path.split("."):
            obj = getattr(obj, part)
    return obj


class ComponentBookmarks:
    """Persists {bookmark_name: dotted_path} so a previously-picked
    component can be found again instantly instead of re-digging through the
    tree -- the "fs adjustable ... store the full working structure as
    names for quicker lookup" piece. Backed by `eco.elements.adjustable.
    AdjustableFS` (the same fs-persisted json value primitive `eco.elements.
    memory.Memory` already uses), so bookmarks survive process restarts and
    are trivially inspectable/editable as plain json.

    Only the *path* (a string) is stored, never the live object -- json
    can't hold one, and a fresh session may not even have built it yet
    (resolving a bookmark is exactly `resolve_path(root, path)`).
    """

    def __init__(self, path=None, namespace_name: Optional[str] = None, name: str = "component_selector_bookmarks"):
        if path is None:
            base_dir = Path.home() / ".eco" / "component_selector"
            fname = f"bookmarks_{namespace_name}.json" if namespace_name else "bookmarks.json"
            path = base_dir / fname
        self._fs = AdjustableFS(path, default_value={}, name=name)

    @property
    def path(self) -> Path:
        return self._fs.file_path

    def all(self) -> dict:
        return dict(self._fs.get_current_value())

    def names(self) -> list:
        return sorted(self.all().keys())

    def get(self, name: str) -> Optional[str]:
        return self.all().get(name)

    def save(self, name: str, dotted_path: str) -> None:
        if not name:
            raise ValueError("bookmark name must not be empty")
        d = self.all()
        d[name] = dotted_path
        self._fs.set_target_value(d).wait()

    def remove(self, name: str) -> None:
        d = self.all()
        if name in d:
            del d[name]
            self._fs.set_target_value(d).wait()


def select_component(
    root,
    kind_filter: str = "All",
    bookmarks: Optional[ComponentBookmarks] = None,
    on_select=None,
    show: bool = True,
):
    """Convenience one-liner, mirroring `Assembly.widget()`'s backend
    choice: build (and, by default, display) a component selector for
    `root` -- the ipywidgets frontend inside a notebook, otherwise the Qt
    frontend. Returns the selector object (`.get_selected()`/`.on_select()`
    work the same on either backend)."""
    from eco.utilities.utilities import is_notebook

    if is_notebook():
        from eco.widgets.component_selector_widget import make_component_selector_widget

        w = make_component_selector_widget(
            root, kind_filter=kind_filter, bookmarks=bookmarks, on_select=on_select
        )
        if show:
            from IPython.display import display

            display(w)
        return w
    else:
        from eco.widgets.component_selector_qt import make_component_selector_qt_window

        return make_component_selector_qt_window(
            root,
            kind_filter=kind_filter,
            bookmarks=bookmarks,
            on_select=on_select,
            auto_start=show,
        )
