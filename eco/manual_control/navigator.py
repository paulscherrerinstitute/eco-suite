"""Tree navigation over eco's own object hierarchy.

An eco beamline is a tree: a Namespace (e.g. `bernina`) holds components,
some of which are Assemblies (mono, slit1, sample chambers, ...) that hold
further Adjustables and sub-Assemblies, down to leaf Adjustables you can
actually move (motors, virtual/derived knobs, PV knobs).

TreeNavigator walks that tree using eco's own structure - no parallel
config or hand-maintained menu:

- A Namespace node is listed from its lazy/initialized registry
  (`all_names`), so the top level can be browsed *by name without
  connecting any hardware*; a component is only resolved (and, if lazy,
  initialized) when you actually descend into or select it.
- A plain Assembly node is listed from its `status_collection` direct
  children (already instantiated by their parent).

Classification:
- **leaf** (controllable): satisfies eco.elements.protocols.Adjustable
  (get_current_value/set_target_value) - you arm it and jog it. Note a
  MotorRecord is an Assembly *and* an Adjustable; it is treated as a
  controllable leaf, not descended into.
- **branch** (navigable): an Assembly that is not itself an Adjustable.
- anything else (e.g. a bare read-only Detector) is hidden from the menu.
"""

from eco.elements.protocols import Adjustable


def is_controllable(obj):
    return isinstance(obj, Adjustable)


def is_container(obj):
    return hasattr(obj, "status_collection") and not isinstance(obj, Adjustable)


def is_namespace(obj):
    return hasattr(obj, "lazy_items") and hasattr(obj, "all_names")


def short_name(obj, fallback="?"):
    if hasattr(obj, "alias") and getattr(obj, "alias") is not None:
        try:
            return obj.alias.get_full_name().split(".")[-1]
        except Exception:
            pass
    return getattr(obj, "name", None) or fallback


class Entry:
    UP = "up"
    ITEM = "item"  # lazy / not-yet-resolved; real kind decided on resolve()
    BRANCH = "branch"
    LEAF = "leaf"

    _MARKERS = {UP: "..", BRANCH: "▸", LEAF: "●", ITEM: "·"}

    def __init__(self, name, kind, getter=None, obj=None):
        self.name = name
        self.kind = kind
        self._getter = getter
        self._obj = obj

    def resolve(self):
        if self._obj is None and self._getter is not None:
            self._obj = self._getter()
        return self._obj

    @property
    def marker(self):
        return self._MARKERS.get(self.kind, "")


def categorize(obj):
    """Return Entry.LEAF / Entry.BRANCH, or None if it should be hidden."""
    if is_controllable(obj):
        return Entry.LEAF
    if is_container(obj):
        return Entry.BRANCH
    return None


def assembly_children(node):
    """Direct (non-recursive) children of an Assembly, from its own
    status_collection - deduped, excluding the node itself."""
    out = []
    seen = set()
    for wref in list(node.status_collection._list):
        obj = wref()
        if obj is None or obj is node or id(obj) in seen:
            continue
        seen.add(id(obj))
        out.append(obj)
    return out


class TreeNavigator:
    def __init__(self, root, root_name=None):
        name = root_name or getattr(root, "name", None) or "root"
        self.stack = [(name, root)]
        self.cursor = 0
        self._cursor_memory = {}

    @property
    def node(self):
        return self.stack[-1][1]

    @property
    def path_names(self):
        return [n for n, _ in self.stack]

    @property
    def depth(self):
        return len(self.stack)

    # --- listing -------------------------------------------------------
    def _namespace_get(self, ns, name):
        def _get():
            if name in ns.initialized_items:
                return ns.initialized_items[name]
            if name in ns.lazy_items:
                return ns.lazy_items[name]
            if name in ns.failed_items:
                return ns.failed_items[name]
            return getattr(ns, name)

        return _get

    def _assembly_children(self, node):
        return assembly_children(node)

    def entries(self):
        node = self.node
        entries = []
        if self.depth > 1:
            entries.append(Entry("..", Entry.UP))

        if is_namespace(node):
            for name in sorted(node.all_names, key=str.lower):
                if name in node.initialized_items:
                    obj = node.initialized_items[name]
                    kind = categorize(obj)
                    if kind is None:
                        continue
                    entries.append(Entry(name, kind, obj=obj))
                else:
                    # Do not resolve lazy items just to list them - keep the
                    # top level connection-free; kind is decided on enter().
                    entries.append(
                        Entry(name, Entry.ITEM, getter=self._namespace_get(node, name))
                    )
        else:
            for obj in self._assembly_children(node):
                kind = categorize(obj)
                if kind is None:
                    continue
                entries.append(Entry(short_name(obj), kind, obj=obj))
        return entries

    # --- movement ------------------------------------------------------
    def clamp_cursor(self, n_entries):
        self.cursor = max(0, min(self.cursor, max(n_entries - 1, 0)))

    def descend(self, name, obj):
        self._cursor_memory[id(self.node)] = self.cursor
        self.stack.append((name, obj))
        self.cursor = 0

    def go_up(self):
        if self.depth > 1:
            self.stack.pop()
            self.cursor = self._cursor_memory.get(id(self.node), 0)

    def jump_to(self, index):
        """Truncate the path to `index` (breadcrumb click)."""
        if 0 <= index < self.depth - 1:
            del self.stack[index + 1 :]
            self.cursor = 0
