"""Export an eco tree to a plain, serializable model the Pi can cache
(preloaded over USB, or fetched once over Bluetooth on connect).

Connection-free by construction: a Namespace's lazy (uninitialized)
components are captured as name-only "unknown/lazy" placeholders - listing
them does NOT connect any hardware (same guarantee we rely on when
browsing the top level). Whatever is already initialized is expanded
recursively. A lazy branch's real children are fetched on demand later,
the first time the operator descends into it (a small message + a PC-side
init), so the snapshot stays cheap.

Node shape:
    {"name": str,
     "kind": "branch" | "leaf" | "unknown",
     "lazy": bool,                       # only on unknown placeholders
     "children": [node, ...] | None}     # None = not-yet-known
"""

from ..navigator import (
    assembly_children,
    categorize,
    is_container,
    is_controllable,
    is_namespace,
    short_name,
    Entry,
)


def export_tree(root, name=None, _depth=0, max_depth=8):
    node = {"name": name or short_name(root) or "root"}

    if is_namespace(root):
        node["kind"] = "branch"
        node["children"] = []
        for cname in sorted(root.all_names, key=str.lower):
            if cname in root.initialized_items:
                child = root.initialized_items[cname]
                kind = categorize(child)
                if kind is None:
                    continue
                node["children"].append(export_tree(child, cname, _depth + 1, max_depth))
            else:
                # name-only placeholder: do NOT resolve (would connect PVs)
                node["children"].append(
                    {"name": cname, "kind": "unknown", "lazy": True, "children": None}
                )
        return node

    if is_controllable(root):
        node["kind"] = Entry.LEAF
        return node

    if is_container(root):
        node["kind"] = Entry.BRANCH
        node["children"] = [] if _depth < max_depth else None
        if _depth < max_depth:
            for child in assembly_children(root):
                kind = categorize(child)
                if kind is None:
                    continue
                node["children"].append(export_tree(child, short_name(child), _depth + 1, max_depth))
        return node

    node["kind"] = "unknown"
    node["children"] = None
    return node


def save_snapshot(root, path, name=None):
    """Write the tree model to a JSON file (for USB preloading)."""
    import json

    with open(path, "w") as f:
        json.dump(export_tree(root, name=name), f, indent=1)
