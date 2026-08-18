"""ipywidgets frontend for eco.widgets.component_selector (notebook use).

Usage:
    from eco.widgets.component_selector_widget import make_component_selector_widget
    sel = make_component_selector_widget(eco.bernina.bernina)
    sel.on_select(lambda name, obj: print("picked", name, obj))
    display(sel)                 # sel is itself a VBox
    name, obj = sel.get_selected()

Embeddable as a sub-component: `sel` is a plain `ipywidgets.VBox`, so it can
be placed inside any other widgets.VBox/HBox/Tab layout.
"""
from typing import Any, Callable, List, Optional

import ipywidgets as widgets

from eco.widgets.component_selector import (
    ComponentBookmarks,
    ComponentNode,
    KIND_ICONS,
    build_tree,
    classify,
    filter_root,
    resolve_path,
)


class ComponentSelectorWidget(widgets.VBox):
    def __init__(
        self,
        root: Any,
        kind_filter: str = "All",
        bookmarks: Optional[ComponentBookmarks] = None,
        on_select: Optional[Callable[[str, Any], None]] = None,
        max_depth: int = 25,
    ):
        self.root = root
        self.max_depth = max_depth
        self.bookmarks = bookmarks or ComponentBookmarks(
            namespace_name=getattr(root, "name", None)
        )
        self._on_select_cbs: List[Callable[[str, Any], None]] = (
            [on_select] if callable(on_select) else []
        )
        self.selected_name: Optional[str] = None
        self.selected_obj: Any = None
        self._tree: Optional[ComponentNode] = None

        self.type_filter = widgets.Dropdown(
            options=["All", "Adjustable", "Detector"],
            value=kind_filter,
            description="Type:",
            layout=widgets.Layout(width="200px"),
        )
        self.search_box = widgets.Text(
            placeholder="filter by name...",
            description="Search:",
            layout=widgets.Layout(width="320px"),
        )
        self.refresh_btn = widgets.Button(
            description="Refresh", icon="refresh", layout=widgets.Layout(width="100px")
        )
        header = widgets.HBox([self.type_filter, self.search_box, self.refresh_btn])

        self.selected_label = widgets.HTML(value="<i>nothing selected</i>")

        self.bookmark_dropdown = widgets.Dropdown(
            options=[], description="Bookmarks:", layout=widgets.Layout(width="320px")
        )
        self.bookmark_goto_btn = widgets.Button(
            description="Go", layout=widgets.Layout(width="50px")
        )
        self.bookmark_name_box = widgets.Text(
            placeholder="bookmark name", layout=widgets.Layout(width="160px")
        )
        self.bookmark_save_btn = widgets.Button(
            description="Save current", layout=widgets.Layout(width="110px")
        )
        self.bookmark_remove_btn = widgets.Button(
            description="Remove", layout=widgets.Layout(width="80px")
        )
        bookmark_row = widgets.HBox(
            [
                self.bookmark_dropdown,
                self.bookmark_goto_btn,
                self.bookmark_name_box,
                self.bookmark_save_btn,
                self.bookmark_remove_btn,
            ]
        )

        self.tree_box = widgets.VBox()

        super().__init__([header, self.selected_label, self.tree_box, bookmark_row])

        self.type_filter.observe(lambda change: self._render(), "value")
        self.search_box.observe(lambda change: self._render(), "value")
        self.refresh_btn.on_click(lambda _: self.refresh(rebuild=True))
        self.bookmark_goto_btn.on_click(lambda _: self._goto_bookmark())
        self.bookmark_save_btn.on_click(lambda _: self._save_bookmark())
        self.bookmark_remove_btn.on_click(lambda _: self._remove_bookmark())

        self.refresh(rebuild=True)

    # -- tree building/rendering -------------------------------------------------

    def refresh(self, rebuild: bool = False) -> None:
        """Re-render. `rebuild=True` re-walks the namespace (picks up
        components initialized/changed since the last build); otherwise just
        re-applies the current filter/search to the cached tree."""
        if rebuild or self._tree is None:
            self._tree = build_tree(self.root, max_depth=self.max_depth)
        self._render()
        self._refresh_bookmark_options()

    def _render(self) -> None:
        filtered = filter_root(
            self._tree, kind=self.type_filter.value, search=self.search_box.value
        )
        self.tree_box.children = self._build_children_widgets(filtered)

    def _build_children_widgets(self, node: ComponentNode) -> list:
        rows = [self._build_node_widget(child) for child in node.children]
        if not rows:
            rows = [widgets.HTML("<i>(no matches)</i>")]
        return rows

    def _build_node_widget(self, node: ComponentNode):
        icon = KIND_ICONS.get(node.kind, "")
        leaf_name = node.name.rsplit(".", 1)[-1]
        if node.children:
            inner = widgets.VBox(self._build_children_widgets(node))
            acc = widgets.Accordion(children=[inner], selected_index=None)
            acc.set_title(0, f"{icon} {leaf_name}")
            select_btn = widgets.Button(
                description="select", layout=widgets.Layout(width="70px")
            )
            select_btn.on_click(lambda _, n=node: self._select(n))
            return widgets.HBox(
                [select_btn, acc],
                layout=widgets.Layout(align_items="flex-start", width="100%"),
            )
        else:
            btn = widgets.Button(
                description=f"{icon} {leaf_name}",
                tooltip=f"{node.name} ({node.kind})",
                layout=widgets.Layout(width="auto"),
            )
            btn.on_click(lambda _, n=node: self._select(n))
            return btn

    # -- selection -----------------------------------------------------------

    def _select(self, node: ComponentNode) -> None:
        self.selected_name = node.name
        self.selected_obj = node.obj
        if node.kind == "lazy":
            self.selected_label.value = (
                f"<b>Selected:</b> {KIND_ICONS['lazy']} {node.name} "
                "<i>(not yet initialized -- resolving it will build it)</i>"
            )
        else:
            self.selected_label.value = (
                f"<b>Selected:</b> {KIND_ICONS.get(node.kind, '')} {node.name} "
                f"<i>({node.kind})</i>"
            )
        self._notify(node.name, node.obj)

    def _notify(self, name: str, obj: Any) -> None:
        for cb in list(self._on_select_cbs):
            try:
                cb(name, obj)
            except Exception as e:
                print(f"component selector on_select callback failed: {e}")

    def get_selected(self):
        return self.selected_name, self.selected_obj

    def on_select(self, cb: Callable[[str, Any], None]) -> None:
        if callable(cb):
            self._on_select_cbs.append(cb)

    # -- bookmarks -------------------------------------------------------------

    def _refresh_bookmark_options(self) -> None:
        names = self.bookmarks.names()
        current = self.bookmark_dropdown.value
        self.bookmark_dropdown.options = names
        if current in names:
            self.bookmark_dropdown.value = current

    def _goto_bookmark(self) -> None:
        name = self.bookmark_dropdown.value
        if not name:
            return
        path = self.bookmarks.get(name)
        try:
            obj = resolve_path(self.root, path)
        except Exception as e:
            self.selected_label.value = (
                f"<span style='color:red'>Could not resolve bookmark "
                f"'{name}' ({path}): {e}</span>"
            )
            return
        kind = classify(obj)
        self.selected_name = path
        self.selected_obj = obj
        self.selected_label.value = (
            f"<b>Selected (bookmark '{name}'):</b> {KIND_ICONS.get(kind, '')} "
            f"{path} <i>({kind})</i>"
        )
        self._notify(path, obj)

    def _save_bookmark(self) -> None:
        name = self.bookmark_name_box.value.strip()
        if not name:
            self.selected_label.value = "<span style='color:red'>Enter a bookmark name first</span>"
            return
        if self.selected_name is None:
            self.selected_label.value = "<span style='color:red'>Select a component first</span>"
            return
        self.bookmarks.save(name, self.selected_name)
        self.bookmark_name_box.value = ""
        self._refresh_bookmark_options()
        self.bookmark_dropdown.value = name

    def _remove_bookmark(self) -> None:
        name = self.bookmark_dropdown.value
        if not name:
            return
        self.bookmarks.remove(name)
        self._refresh_bookmark_options()


def make_component_selector_widget(
    root: Any,
    kind_filter: str = "All",
    bookmarks: Optional[ComponentBookmarks] = None,
    on_select: Optional[Callable[[str, Any], None]] = None,
    max_depth: int = 25,
) -> ComponentSelectorWidget:
    """Convenience factory, mirrors make_assembly_widget's naming."""
    return ComponentSelectorWidget(
        root,
        kind_filter=kind_filter,
        bookmarks=bookmarks,
        on_select=on_select,
        max_depth=max_depth,
    )
