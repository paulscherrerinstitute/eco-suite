"""
Memory browser: browse / filter / unfold / recall / save / export an
Assembly's memory (see `eco.elements.memory.Memory`) from Qt, ipywidgets, or
Tk -- the GUI counterpart of the terminal `some_assembly.memory()` picker.

This module never reimplements `Memory`'s own logic. `memorize()`,
`recall()`, `get_memory()`, `get_recall_dict()`, `ancestor_memories()`,
`get_ancestor_recall_dict()` (`eco/elements/memory.py`) stay the single
source of truth for what gets read from / written to disk (and to elog);
everything here only calls them and renders the results, so the on-disk
memory format and every existing `.memory.*` call site are completely
unaffected by this module's existence.

    from eco.widgets.memory_widget import make_memory_browser
    make_memory_browser(my_assembly)  # picks Qt/ipywidgets/Tk, like Assembly.widget()

Also reachable via a "memories" button on the assembly display widget itself
(`eco.widgets.display_qt`/`display_widget`/`display_tk`), shown only when the
assembly actually has a `.memory` (i.e. `eco.elements.memory.global_memory_dir`
is set) -- assemblies without one see no change at all.

Besides an object's own stored memories, the browser can optionally also
show memories stored on an *ancestor* Assembly (a "show parent memories too"
toggle) that happen to also cover this object's own components -- see
`Memory.ancestor_memories`/`get_ancestor_recall_dict`. Such rows are tagged
and colour-coded per ancestor, with a legend, and are otherwise handled
identically to the object's own memories (loaded, diffed, and recalled the
same way -- only ever through *this* object's own `.memory`, applying just
the slice of the parent's snapshot that belongs to this object).

Saving a new memory can optionally be narrowed down to hand-picked items
instead of capturing everything the chosen selection resolves to -- an
opt-in "choose items…" checkbox (unchecked by default) next to Save reveals
a per-item checklist (via `Memory.get_memorize_candidates`), and only the
checked items are passed on to `Memory.memorize(pick_items=[...])`. Same
idea as the terminal's own `memorize(pick_items=True)`, just via an inline
GUI checklist instead of a `simple_term_menu` prompt, since a terminal menu
can't run inside a GUI event loop.

A memory can optionally be tagged as a named preset (`Memory.memorize`'s
`preset_varname`, reachable afterwards as `<assembly>.memory.presets.<name>`)
-- an optional "preset name" field next to Save does this at creation time;
an existing stored entry's tag can also be set, changed, or removed
afterwards (`Memory.set_preset_name`) via the "Preset tag" field shown once
that entry is loaded from the overview list. Tagged entries show their
preset name in the overview list like any other field -- a preset is just a
regular memory entry with that one extra tag, never a separate store.
"""
import itertools
import traceback
from datetime import datetime
from pathlib import Path

from eco.elements.memory import name2obj

_JSON_FILETYPES_QT = "JSON (*.json);;All files (*)"
_JSON_FILETYPES_TK = [("JSON", "*.json"), ("all files", "*")]

# pastel palette for colour-coding overview rows sourced from a parent's
# memory (one colour per distinct ancestor, cycled in first-seen order) --
# light enough to work as a background under plain black text in Qt/Tk/
# ipywidgets' default (light) themes.
_ANCESTOR_PALETTE = [
    "#ffe0b2", "#c8e6c9", "#bbdefb", "#e1bee7", "#ffccbc", "#d7ccc8",
]


# ---------------------------------------------------------------------------
# shared, toolkit-agnostic data model -- pure python, reused by all backends
# ---------------------------------------------------------------------------


def _overview_rows(memory):
    """[{key, date, message, groups}, ...] for every stored memory, newest
    first -- the same index data `Memory.__str__`/`Memory.__call__` already
    render, just as structured rows instead of a formatted string."""
    memory.setup_path()
    mem = memory._memories()
    rows = []
    for key, content in mem.items():
        try:
            date = datetime.fromisoformat(key)
        except Exception:
            date = None
        cats = content.get("categories", memory.categories)
        groups = sorted(set(itertools.chain.from_iterable(cats.values())))
        rows.append(
            {
                "key": key,
                "date": date,
                "message": content.get("message", ""),
                "groups": groups,
                "presetname": content.get("presetname") or "",
            }
        )
    rows.reverse()
    return rows


def _ancestor_overview_rows(memory, selection=None):
    """[{key, date, message, groups, ancestor, ancestor_name}, ...] -- one
    row per memory stored *on an ancestor Assembly* (see
    `Memory.ancestor_memories`) that actually covers this object (i.e.
    `Memory.get_ancestor_recall_dict` for it is non-empty). Ancestors in
    nearest-first order; each ancestor's own entries newest-first. Purely
    additive/opt-in browsing -- never touches this object's own store, and
    reads every ancestor's every stored entry to check relevance, so this is
    only meant to be called on demand (a "show parent memories too" toggle),
    not on every refresh."""
    rows = []
    for ancestor, ancestor_memory in memory.ancestor_memories():
        ancestor_name = ancestor.alias.get_full_name()
        for row in _overview_rows(ancestor_memory):
            try:
                entry = ancestor_memory.get_memory(key=row["key"])
                sliced = memory.get_ancestor_recall_dict(
                    ancestor, entry, selection=selection
                )
            except Exception:
                continue
            if not sliced:
                continue
            tagged = dict(row)
            tagged["ancestor"] = ancestor
            tagged["ancestor_name"] = ancestor_name
            rows.append(tagged)
    return rows


def _ancestor_color_map(ancestor_names):
    """Stable {ancestor_full_name: hex_color} assignment, cycling
    `_ANCESTOR_PALETTE` in first-seen order -- used to colour overview rows
    sourced from a parent's memory, with a legend mapping colour back to
    ancestor name."""
    names = []
    for n in ancestor_names:
        if n not in names:
            names.append(n)
    return {n: _ANCESTOR_PALETTE[i % len(_ANCESTOR_PALETTE)] for i, n in enumerate(names)}


def _available_recall_groups(memory, mem_dict):
    """Recall-eligible selections (see `Memory.categories["recall"]`) that
    a loaded, per-entry memory dict actually captured -- may be a subset of
    `memory.categories["recall"]` (an older/foreign memory can predate a
    group, or use a since-renamed name; see `Memory.get_recall_dict`'s
    docstring for a real example), and is deliberately narrower than "every
    selection this entry has" (`memory.entry_selection_names`): track-only
    groups (e.g. plain "display") are bookkeeping, not meant to be offered
    as a recall target in this per-group checkbox list."""
    return [g for g in memory.categories["recall"] if g in memory.entry_selection_names(mem_dict)]


def _filter_overview_rows(rows, tag):
    """Narrow `_overview_rows`/`_ancestor_overview_rows` output to entries
    whose stored `categories` include `tag` -- the "which memories are even
    listed" filter, distinct from `_diff_rows`'s group filter which only
    ever applies to rows within one already-opened memory. `tag` of `None`
    or "all" returns every row unfiltered."""
    if not tag or tag == "all":
        return rows
    return [r for r in rows if tag in r["groups"]]


def _known_selection_tags(rows):
    """Every distinct group tag seen across a set of overview rows, sorted,
    always including "settings" (today's universal default) even when this
    particular assembly has no stored memories yet -- so a selection filter
    control always has a sensible option to land on."""
    tags = set()
    for r in rows:
        tags.update(r["groups"])
    tags.add("settings")
    return sorted(tags)


def _diff_str(present, memory_value, changed, available):
    """Numeric-delta string mirroring `Memory.get_memory_difference_str`'s
    own "difference" column (`memory_value - present`, "+g"-formatted),
    reused here as plain data instead of an ANSI/HTML table string."""
    if not available:
        return "?"
    if not changed:
        return "=="
    try:
        return f"{memory_value - present:+g}"
    except TypeError:
        return "changed"


def _diff_rows(memory, mem_dict, selection=None):
    """[{name, present, memory, changed, available, group, diff}, ...] for
    a loaded, per-entry memory dict, reusing `Memory.get_recall_dict` for
    the selection merge -- the same present-vs-recall comparison
    `Memory.get_memory_difference_str` does internally, exposed as data
    instead of a formatted table string."""
    rec = memory.get_recall_dict(mem_dict, selection=selection)
    rows = []
    for name, recall_value in rec.items():
        try:
            present_value = name2obj(memory.obj_parent(), name).get_current_value()
            available = True
        except Exception:
            present_value = None
            available = False
        changed = available and (present_value != recall_value)
        rows.append(
            {
                "name": name,
                "present": present_value,
                "memory": recall_value,
                "changed": changed,
                "available": available,
                "group": name.split(".", 1)[0],
                "diff": _diff_str(present_value, recall_value, changed, available),
            }
        )
    return rows


def _search_matches(text, *fields):
    """True when every whitespace-separated word of `text` occurs
    (case-insensitively) in at least one of `fields` -- "mono energy"
    finds "mono.energy" as well as a message mentioning both. An empty
    `text` matches everything."""
    words = (text or "").lower().split()
    if not words:
        return True
    hay = " ".join(str(f) for f in fields if f is not None).lower()
    return all(w in hay for w in words)


def _overview_row_matches(row, text):
    """Search over everything an overview row shows: message, preset name,
    groups, date, key and (for a parent's memory) the ancestor's name."""
    date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row.get("date") else ""
    return _search_matches(
        text, row.get("message"), row.get("presetname"), " ".join(row.get("groups", [])),
        date_str, row.get("key"), row.get("ancestor_name"),
    )


def _name_tree(items, name=lambda item: item["name"]):
    """Hierarchy of `items` by their dot-separated names (the same split as
    escape's `utilities.unflatten_dictionary`), for rendering as a foldable
    tree. Returns the root node; every node is
    ``{"label", "path", "item", "children": [node, ...]}`` -- `item` is the
    row whose name is exactly `path` (or None for a pure grouping level),
    `children` keep first-seen order.

    A grouping level with a single child and no row of its own is merged
    into that child ("a" > "b" > "c" becomes one "a.b.c" node), so a lone
    deep row costs one line, not one fold level per name segment -- the
    same "single rows get no group of their own" rule
    `eco.utilities.tables.section_row_styles` applies to the terminal table. A name can
    be both a row and a prefix of other rows (an Assembly that is itself an
    Adjustable); such a node has an `item` *and* `children`."""
    root = {"label": "", "path": "", "item": None, "children": {}}
    for it in items:
        parts = str(name(it)).split(".")
        node = root
        for i, seg in enumerate(parts):
            node = node["children"].setdefault(
                seg,
                {"label": seg, "path": ".".join(parts[: i + 1]), "item": None, "children": {}},
            )
        if node["item"] is None:
            node["item"] = it

    def _finish(node):
        kids = []
        for child in node["children"].values():
            while child["item"] is None and len(child["children"]) == 1:
                (only,) = child["children"].values()
                child = dict(only, label=f"{child['label']}.{only['label']}")
            kids.append(_finish(child))
        return dict(node, children=kids)

    return _finish(root)


def _tree_items(node):
    """Every row in `node`'s subtree (its own first), depth-first."""
    out = [node["item"]] if node["item"] is not None else []
    for child in node["children"]:
        out.extend(_tree_items(child))
    return out


def _visible_detail_rows(rows, changed_only=False, search=""):
    """The `_diff_rows` rows a detail view currently shows -- what "select
    all/none" and Recall act on, so a search never recalls rows it hides."""
    return [
        r for r in rows
        if (r["changed"] or not changed_only) and _search_matches(search, r["name"])
    ]


def _visible_pick_items(pairs, search=""):
    """`Memory.get_memorize_candidates` pairs a save picker currently shows."""
    return [p for p in pairs if _search_matches(search, p[1], p[0])]


def _default_export_name(memory, label):
    base = memory.obj_parent().alias.get_full_name()
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(label))
    return f"{base}_{safe}.json"


def _export_to_file(mem_dict, path):
    """Write a loaded memory dict to an arbitrary file, in exactly the same
    on-disk shape `Memory.memorize()` writes for every stored entry
    (`AdjustableFS`'s `{"value": ...}` wrapper) -- so the exported file can
    later be handed straight back to `Memory.recall(input_obj=path)` /
    `Memory.get_memory(input_obj=path)`, both of which already read that
    format directly. Purely additive: never touches the internal memory
    store, which `memorize()` keeps writing to on its own."""
    from eco.elements.adjustable import AdjustableFS

    path = Path(path)
    target = AdjustableFS(path, default_value=mem_dict)
    # AdjustableFS only writes `default_value` when the file didn't already
    # exist; write explicitly so exporting to an existing path also works.
    target._write_value(mem_dict)
    return path


def _export_label(state):
    """Best-effort label for the currently loaded memory, for a default
    export filename -- whichever of its own stored key, the ancestor it was
    sourced from, or the file it was loaded from is actually set."""
    if state.get("key"):
        return state["key"]
    if state.get("source_ancestor_name"):
        return state["source_ancestor_name"]
    if state.get("path"):
        return Path(state["path"]).stem
    return "memory"


def _format_value(value):
    import enum

    if isinstance(value, enum.Enum):
        return value.name
    return str(value)


def _recall_error_status(recall_error):
    """Status text for a `Memory.recall()` call that raised. `recall()` sets
    every changed value first and only then waits for each to confirm -- an
    exception here (e.g. a slow/flaky device's confirmation read timing out)
    does not mean nothing happened; some or all of the underlying
    `set_target_value` calls may already have landed. Callers refresh the
    display regardless of this exception, so it reflects reality either
    way; this message just avoids a flat, potentially misleading "failed"."""
    return (
        f"recall command sent, but confirming completion failed "
        f"({recall_error}) -- check values below, the change may still "
        f"have applied"
    )


# ---------------------------------------------------------------------------
# Qt backend
# ---------------------------------------------------------------------------


_EMPTY_STATE = {
    "key": None, "path": None, "mem": None, "rows": [], "checked": {},
    "source_ancestor_name": None, "visible": [],
}

# Above this many shown rows a check tree opens with its groups folded
# (unless a search is active, which always unfolds what it found).
_AUTO_EXPAND_MAX_ROWS = 50

# Smallest size a section gets when it is detached into its own window --
# the docked size is often just a strip of the main window.
_FLOAT_MIN_SIZE = {
    "overview": (820, 420), "detail": (900, 600), "save": (700, 160), "pick": (800, 600),
}


class MemoryBrowserQt:
    """Qt memory browser. `window` is a QMainWindow whose four sections --
    stored memories, details of the loaded memory (recall), save, and the
    optional items-to-save picker -- are dock widgets: resizable against
    each other, movable, and each detachable into its own window (its
    "detach" button, or double-click on its title bar; double-click again
    or "attach" to put it back). When the whole browser is docked into the
    eco desktop (`item_menu_qt.host`) the sections still detach.

    Both checklists (recall, items to save) are trees over the dotted item
    names (`_name_tree`): a group's checkbox (de)selects everything below
    it, a partly selected group shows a partial check. Recall and Save act
    on the checked rows that are currently *shown* -- a search or "changed
    only" never sends anything it hides."""

    def __init__(self, assembly, parent=None):
        self.assembly = assembly
        self.memory = assembly.memory
        self.parent = parent
        self.window = None
        self._state = dict(_EMPTY_STATE, checked={})
        self._group_checks = {}
        self._overview_all = []
        self._overview = []
        self._detail_expanded = {}
        self._pick_pairs = []
        self._pick_checked = {}
        self._pick_expanded = {}
        self._pick_visible = []
        self._suppress_item_changed = False
        self.docks = {}
        self._detach_buttons = {}
        self._build()

    # --- construction ---------------------------------------------------

    def _detach_button(self, key):
        from qtpy import QtWidgets

        btn = QtWidgets.QToolButton()
        btn.setText("detach")
        btn.setToolTip("show this section in its own window (or double-click its title bar)")
        btn.clicked.connect(lambda _c=False, k=key: self._toggle_floating(k))
        self._detach_buttons[key] = btn
        return btn

    def _make_dock(self, key, title, widget):
        from qtpy import QtWidgets

        dock = QtWidgets.QDockWidget(title, self.window)
        dock.setObjectName(f"memory_browser_{key}")
        dock.setWidget(widget)
        dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        dock.topLevelChanged.connect(lambda floating, k=key: self._on_floating_changed(k, floating))
        self.docks[key] = dock
        return dock

    def _toggle_floating(self, key):
        dock = self.docks[key]
        dock.setFloating(not dock.isFloating())

    def _on_floating_changed(self, key, floating):
        try:
            btn = self._detach_buttons.get(key)
            if btn is not None:
                btn.setText("attach" if floating else "detach")
                btn.setToolTip(
                    "put this section back into the memory browser window"
                    if floating
                    else "show this section in its own window (or double-click its title bar)"
                )
            if floating:
                dock = self.docks[key]
                min_w, min_h = _FLOAT_MIN_SIZE.get(key, (600, 400))
                dock.resize(max(dock.width(), min_w), max(dock.height(), min_h))
        except RuntimeError:
            pass  # widget already deleted

    def _build(self):
        from qtpy import QtWidgets, QtCore

        full_name = self.assembly.alias.get_full_name()
        self.window = QtWidgets.QMainWindow(self.parent)
        self.window.setWindowTitle(f"Memories - {full_name}")
        if self.parent is None:
            self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        # so this.window really is destroyed (not just hidden) when closed
        # via its own native close (X) button -- otherwise the destroyed
        # hook below never fires there, and a caller's "already open?
        # just raise it" check (see AxisPTZStreamQt._open_memories) keeps
        # seeing a stale, non-None .window that's actually closed
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.window.setDockNestingEnabled(True)
        # QMainWindow needs a central widget; a hidden one lets the docks
        # take all of the space, so every section can be detached.
        placeholder = QtWidgets.QWidget()
        placeholder.hide()
        self.window.setCentralWidget(placeholder)

        overview_dock = self._make_dock("overview", "Stored memories", self._build_overview())
        detail_dock = self._make_dock(
            "detail", "Loaded memory - recall (checkbox selects)", self._build_detail()
        )
        save_dock = self._make_dock("save", "Save current state as a new memory", self._build_save())
        pick_dock = self._make_dock("pick", "Items to save", self._build_pick())

        top = QtCore.Qt.TopDockWidgetArea
        self.window.addDockWidget(top, overview_dock)
        self.window.addDockWidget(top, detail_dock)
        self.window.splitDockWidget(overview_dock, detail_dock, QtCore.Qt.Vertical)
        self.window.addDockWidget(QtCore.Qt.BottomDockWidgetArea, save_dock)
        # the two big checklists share the space as tabs (recall vs. save
        # are used at different times); either can be detached to see both
        self.window.tabifyDockWidget(detail_dock, pick_dock)
        pick_dock.hide()

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        status_bar = self.window.statusBar()
        status_bar.addWidget(self.status_label, 1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.window.close)
        status_bar.addPermanentWidget(close_btn)

        self._refresh_overview()
        self.window.resize(1000, 900)
        self.window.resizeDocks([overview_dock, detail_dock], [260, 480], QtCore.Qt.Vertical)
        # so a stale, already-deleted window can't be mistaken for a still-
        # open one (e.g. by a caller's "already open, just raise it" check
        # -- see eco.widgets.camera_stream_qt.AxisPTZStreamQt._open_memories)
        # when the user closes this via the window's own native close (X)
        # button rather than through this class's own Close button/API
        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))
        self.window.show()

    def _build_overview(self):
        from qtpy import QtWidgets

        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(4, 4, 4, 4)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(QtWidgets.QLabel("Selection:"))
        self.overview_selection_combo = QtWidgets.QComboBox()
        self.overview_selection_combo.setEditable(True)
        self.overview_selection_combo.currentTextChanged.connect(self._render_overview)
        filter_row.addWidget(self.overview_selection_combo)
        self.show_parents_cb = QtWidgets.QCheckBox("show parent memories too")
        self.show_parents_cb.stateChanged.connect(self._refresh_overview)
        filter_row.addWidget(self.show_parents_cb)
        self.presets_only_cb = QtWidgets.QCheckBox("presets only")
        self.presets_only_cb.stateChanged.connect(self._render_overview)
        filter_row.addWidget(self.presets_only_cb)
        filter_row.addStretch(1)
        filter_row.addWidget(self._detach_button("overview"))
        outer.addLayout(filter_row)

        search_row = QtWidgets.QHBoxLayout()
        self.overview_search = QtWidgets.QLineEdit()
        self.overview_search.setPlaceholderText(
            "search message, preset name, group, date… (all words must match)"
        )
        self.overview_search.setClearButtonEnabled(True)
        self.overview_search.textChanged.connect(self._render_overview)
        search_row.addWidget(self.overview_search, 1)
        self.overview_count_label = QtWidgets.QLabel("")
        search_row.addWidget(self.overview_count_label)
        outer.addLayout(search_row)

        self.list = QtWidgets.QTreeWidget()
        self.list.setHeaderLabels(["Date", "Preset", "Message", "Groups"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setUniformRowHeights(True)
        self.list.setSortingEnabled(True)
        self.list.sortByColumn(0, _descending())
        self.list.itemClicked.connect(self._on_pick_stored)
        self.list.itemActivated.connect(self._on_pick_stored)
        self.list.setMinimumHeight(150)
        outer.addWidget(self.list, 1)

        self.legend_label = QtWidgets.QLabel("")
        self.legend_label.setWordWrap(True)
        outer.addWidget(self.legend_label)

        load_row = QtWidgets.QHBoxLayout()
        load_file_btn = QtWidgets.QPushButton("Load from file…")
        load_file_btn.clicked.connect(self._on_load_file)
        refresh_btn = QtWidgets.QPushButton("Refresh list")
        refresh_btn.clicked.connect(self._refresh_overview)
        load_row.addWidget(load_file_btn)
        load_row.addWidget(refresh_btn)
        load_row.addSpacing(16)
        load_row.addWidget(QtWidgets.QLabel("Preset tag (loaded entry):"))
        self.preset_tag_edit = QtWidgets.QLineEdit()
        self.preset_tag_edit.setPlaceholderText("empty clears the tag")
        load_row.addWidget(self.preset_tag_edit)
        apply_preset_btn = QtWidgets.QPushButton("Apply")
        apply_preset_btn.setToolTip(
            "set/change/clear just the preset name tag on the loaded entry "
            "-- does not touch its captured values or delete it"
        )
        apply_preset_btn.clicked.connect(self._on_apply_preset_name)
        load_row.addWidget(apply_preset_btn)
        load_row.addStretch(1)
        delete_entry_btn = QtWidgets.QPushButton("Delete this memory entry…")
        delete_entry_btn.setStyleSheet("color: #c62828;")
        delete_entry_btn.setToolTip(
            "permanently remove the WHOLE loaded memory entry -- every "
            "value it captured, not just its preset tag"
        )
        delete_entry_btn.clicked.connect(self._on_delete_entry)
        load_row.addWidget(delete_entry_btn)
        outer.addLayout(load_row)
        return w

    def _build_detail(self):
        from qtpy import QtWidgets

        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(4, 4, 4, 4)

        head = QtWidgets.QHBoxLayout()
        self.loaded_label = QtWidgets.QLabel("<i>no memory loaded -- pick one from the list</i>")
        head.addWidget(self.loaded_label, 1)
        head.addWidget(self._detach_button("detail"))
        outer.addLayout(head)

        self.group_row = QtWidgets.QHBoxLayout()
        self.group_row.addWidget(QtWidgets.QLabel("Keyword groups:"))
        group_wrap = QtWidgets.QHBoxLayout()
        group_wrap.addLayout(self.group_row)
        group_wrap.addStretch(1)
        outer.addLayout(group_wrap)

        filter_row = QtWidgets.QHBoxLayout()
        self.changed_only_cb = QtWidgets.QCheckBox("show changed only")
        self.changed_only_cb.setChecked(True)
        self.changed_only_cb.stateChanged.connect(self._render_detail)
        filter_row.addWidget(self.changed_only_cb)
        self.detail_search = QtWidgets.QLineEdit()
        self.detail_search.setPlaceholderText("search item names… (e.g. \"mono energy\")")
        self.detail_search.setClearButtonEnabled(True)
        self.detail_search.textChanged.connect(self._render_detail)
        filter_row.addWidget(self.detail_search, 1)
        expand_btn = QtWidgets.QPushButton("unfold all")
        expand_btn.clicked.connect(lambda: self._set_tree_expanded(self.tree, self._detail_expanded, True))
        collapse_btn = QtWidgets.QPushButton("fold all")
        collapse_btn.clicked.connect(lambda: self._set_tree_expanded(self.tree, self._detail_expanded, False))
        filter_row.addWidget(expand_btn)
        filter_row.addWidget(collapse_btn)
        outer.addLayout(filter_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["name", "present", "memory", "diff", "status"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.itemExpanded.connect(lambda it: self._remember_expanded(self._detail_expanded, it, True))
        self.tree.itemCollapsed.connect(lambda it: self._remember_expanded(self._detail_expanded, it, False))
        outer.addWidget(self.tree, 1)

        select_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("select shown")
        select_all_btn.setToolTip("check every row currently shown (search / changed-only apply)")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn = QtWidgets.QPushButton("unselect shown")
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all_btn)
        select_row.addWidget(select_none_btn)
        self.detail_count_label = QtWidgets.QLabel("")
        select_row.addWidget(self.detail_count_label)
        select_row.addStretch(1)
        recall_btn = QtWidgets.QPushButton("Recall selected values")
        recall_btn.setToolTip("recall the checked rows that are currently shown")
        recall_btn.clicked.connect(self._on_recall)
        export_btn = QtWidgets.QPushButton("Export to file…")
        export_btn.clicked.connect(self._on_export)
        select_row.addWidget(recall_btn)
        select_row.addWidget(export_btn)
        outer.addLayout(select_row)
        return w

    def _build_save(self):
        from qtpy import QtWidgets

        w = QtWidgets.QWidget()
        save_vbox = QtWidgets.QVBoxLayout(w)
        save_vbox.setContentsMargins(4, 4, 4, 4)
        save_layout = QtWidgets.QHBoxLayout()
        self.message_edit = QtWidgets.QLineEdit()
        self.message_edit.setPlaceholderText("message for this memory…")
        save_layout.addWidget(self.message_edit, 1)
        save_layout.addWidget(QtWidgets.QLabel("selection:"))
        self.save_selection_combo = QtWidgets.QComboBox()
        self.save_selection_combo.setEditable(True)
        self.save_selection_combo.addItems(["settings", "all"])
        self.save_selection_combo.setCurrentText("settings")
        self.save_selection_combo.setToolTip(
            "which status_collection selection to capture -- \"settings\" "
            "(default), \"all\" (everything currently registered), or a "
            "custom tag"
        )
        save_layout.addWidget(self.save_selection_combo)
        self.elog_cb = QtWidgets.QCheckBox("post to elog")
        self.elog_cb.setChecked(True)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.clicked.connect(self._on_save)
        save_layout.addWidget(self.elog_cb)
        save_layout.addWidget(save_btn)
        save_layout.addWidget(self._detach_button("save"))
        save_vbox.addLayout(save_layout)

        preset_layout = QtWidgets.QHBoxLayout()
        preset_layout.addWidget(QtWidgets.QLabel("preset name:"))
        self.save_preset_edit = QtWidgets.QLineEdit()
        self.save_preset_edit.setPlaceholderText("optional -- leave empty for a plain memory")
        self.save_preset_edit.setToolTip(
            "tag this memory as a named preset, reachable afterwards as "
            "<assembly>.memory.presets.<name>(). Reusing an existing name "
            "redirects that preset to this new entry; the old entry stays "
            "around as a plain memory."
        )
        preset_layout.addWidget(self.save_preset_edit, 1)
        # opt-in, non-default item picker: unchecked, its section stays
        # hidden and Save captures everything the selection resolves to,
        # exactly as before this existed.
        self.pick_items_cb = QtWidgets.QCheckBox("choose items…")
        self.pick_items_cb.setToolTip(
            "narrow this save down to hand-picked items instead of "
            "capturing everything the selection above resolves to "
            "(opens the \"Items to save\" section)"
        )
        self.pick_items_cb.stateChanged.connect(self._on_toggle_pick_items)
        preset_layout.addWidget(self.pick_items_cb)
        save_vbox.addLayout(preset_layout)
        return w

    def _build_pick(self):
        from qtpy import QtWidgets

        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(4, 4, 4, 4)
        row = QtWidgets.QHBoxLayout()
        self.pick_search = QtWidgets.QLineEdit()
        self.pick_search.setPlaceholderText("search item names…")
        self.pick_search.setClearButtonEnabled(True)
        self.pick_search.textChanged.connect(self._render_pick_items)
        row.addWidget(self.pick_search, 1)
        refresh_items_btn = QtWidgets.QPushButton("Refresh items")
        refresh_items_btn.clicked.connect(self._refresh_pick_items)
        row.addWidget(refresh_items_btn)
        pick_all_btn = QtWidgets.QPushButton("all shown")
        pick_all_btn.clicked.connect(lambda: self._set_all_pick_items(True))
        row.addWidget(pick_all_btn)
        pick_none_btn = QtWidgets.QPushButton("none shown")
        pick_none_btn.clicked.connect(lambda: self._set_all_pick_items(False))
        row.addWidget(pick_none_btn)
        expand_btn = QtWidgets.QPushButton("unfold all")
        expand_btn.clicked.connect(
            lambda: self._set_tree_expanded(self.pick_items_list, self._pick_expanded, True)
        )
        collapse_btn = QtWidgets.QPushButton("fold all")
        collapse_btn.clicked.connect(
            lambda: self._set_tree_expanded(self.pick_items_list, self._pick_expanded, False)
        )
        row.addWidget(expand_btn)
        row.addWidget(collapse_btn)
        row.addWidget(self._detach_button("pick"))
        outer.addLayout(row)

        self.pick_items_list = QtWidgets.QTreeWidget()
        self.pick_items_list.setHeaderLabels(["name", "value", "group"])
        self.pick_items_list.setUniformRowHeights(True)
        self.pick_items_list.setAlternatingRowColors(True)
        self.pick_items_list.itemChanged.connect(self._on_pick_item_changed)
        self.pick_items_list.itemExpanded.connect(
            lambda it: self._remember_expanded(self._pick_expanded, it, True)
        )
        self.pick_items_list.itemCollapsed.connect(
            lambda it: self._remember_expanded(self._pick_expanded, it, False)
        )
        outer.addWidget(self.pick_items_list, 1)
        self.pick_count_label = QtWidgets.QLabel("")
        outer.addWidget(self.pick_count_label)
        return w

    # --- generic check tree -----------------------------------------------

    def _fill_check_tree(self, tree, root, key, make_leaf, summary, checked, expanded, auto_expand):
        """(Re)build `tree` from a `_name_tree` root. `key(item)` is the
        row's full name (what `checked` is keyed by), `make_leaf(item,
        label)` returns the leaf's QTreeWidgetItem, `summary(items)` the
        non-name columns of a group row. Group checkboxes are Qt's
        auto-tristate ones: derived from, and propagating to, their
        children. Fold state is kept per group path in `expanded`; groups
        without a remembered state open when `auto_expand`."""
        from qtpy import QtCore, QtGui, QtWidgets

        Qt = QtCore.Qt
        bold = QtGui.QFont()
        bold.setBold(True)

        def attach(parent, item):
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)

        def add_leaf(parent, item, label):
            name = key(item)
            w = make_leaf(item, label)
            w.setFlags(w.flags() | Qt.ItemIsUserCheckable)
            w.setData(0, Qt.UserRole, name)
            w.setToolTip(0, name)
            attach(parent, w)
            w.setCheckState(0, Qt.Checked if checked.get(name, True) else Qt.Unchecked)

        def add(parent, node):
            if not node["children"]:
                add_leaf(parent, node["item"], node["label"])
                return
            items = _tree_items(node)
            g = QtWidgets.QTreeWidgetItem([node["label"]] + list(summary(items)))
            g.setFlags(g.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            g.setData(0, Qt.UserRole + 1, node["path"])
            g.setToolTip(
                0, f"{node['path']} -- {len(items)} item(s); its checkbox (de)selects all of them"
            )
            g.setFont(0, bold)
            attach(parent, g)
            g.setCheckState(0, Qt.Checked)  # before children: Qt derives it from them afterwards
            if node["item"] is not None:
                add_leaf(g, node["item"], f"{node['label']} (own value)")
            for child in node["children"]:
                add(g, child)
            g.setExpanded(expanded.get(node["path"], auto_expand))

        self._suppress_item_changed = True
        tree.setUpdatesEnabled(False)
        try:
            tree.clear()
            for child in root["children"]:
                add(None, child)
            for col in range(tree.columnCount()):
                tree.resizeColumnToContents(col)
        finally:
            tree.setUpdatesEnabled(True)
            self._suppress_item_changed = False

    def _remember_expanded(self, expanded, item, value):
        from qtpy import QtCore

        if self._suppress_item_changed:
            return
        path = item.data(0, QtCore.Qt.UserRole + 1)
        if path is not None:
            expanded[path] = value

    def _set_tree_expanded(self, tree, expanded, value):
        from qtpy import QtCore, QtWidgets

        self._suppress_item_changed = True
        try:
            it = QtWidgets.QTreeWidgetItemIterator(tree)
            while it.value():
                item = it.value()
                path = item.data(0, QtCore.Qt.UserRole + 1)
                if path is not None:
                    item.setExpanded(value)
                    expanded[path] = value
                it += 1
        finally:
            self._suppress_item_changed = False

    # --- overview ---------------------------------------------------------

    def _set_status(self, text, error=False):
        color = "#c62828" if error else "#2e7d32"
        self.status_label.setText(f"<span style='color:{color}'>{text}</span>")

    def _refresh_overview(self, *_a):
        # *_a swallows whatever positional arg the triggering Qt signal
        # passes (a bool from a button, an int from a checkbox) -- neither
        # is used, both re-read live state. Reads the stores from disk;
        # filtering/searching only re-renders (`_render_overview`).
        own_rows = _overview_rows(self.memory)
        all_rows = list(own_rows)
        if self.show_parents_cb.isChecked():
            all_rows += _ancestor_overview_rows(self.memory)
        self._overview_all = all_rows
        tags = _known_selection_tags(all_rows)
        combo = self.overview_selection_combo
        current = combo.currentText() or "settings"
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(["all"] + tags)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(current)
        combo.blockSignals(False)
        self._render_overview()

    def _render_overview(self, *_a):
        from qtpy import QtWidgets, QtGui, QtCore

        tag = self.overview_selection_combo.currentText() or "settings"
        rows = _filter_overview_rows(self._overview_all, tag)
        if self.presets_only_cb.isChecked():
            rows = [r for r in rows if r.get("presetname")]
        search = self.overview_search.text()
        rows = [r for r in rows if _overview_row_matches(r, search)]
        self._overview = rows

        ancestor_names = [r["ancestor_name"] for r in rows if "ancestor_name" in r]
        color_map = _ancestor_color_map(ancestor_names)
        self.list.setSortingEnabled(False)
        self.list.clear()
        for idx, row in enumerate(rows):
            date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "?"
            groups = ",".join(row["groups"])
            message = row["message"]
            ancestor_name = row.get("ancestor_name")
            if ancestor_name:
                message = f"[{ancestor_name}] {message}"
            item = QtWidgets.QTreeWidgetItem(
                [date_str, row.get("presetname") or "", message, groups]
            )
            item.setData(0, QtCore.Qt.UserRole, idx)
            item.setToolTip(2, message)
            if ancestor_name:
                for col in range(4):
                    item.setBackground(col, QtGui.QColor(color_map[ancestor_name]))
            self.list.addTopLevelItem(item)
        self.list.setSortingEnabled(True)
        for col in range(4):
            self.list.resizeColumnToContents(col)
        self.overview_count_label.setText(f"{len(rows)} of {len(self._overview_all)} shown")

        if color_map:
            self.legend_label.setText(
                "Parent memory colours: " + "; ".join(
                    f'<span style="background-color:{c};">&nbsp;{n}&nbsp;</span>'
                    for n, c in color_map.items()
                )
            )
        else:
            self.legend_label.setText("")

    def _on_pick_stored(self, item, column=0):
        from qtpy import QtCore

        idx = item.data(0, QtCore.Qt.UserRole)
        if idx is None or idx >= len(self._overview):
            return
        row = self._overview[idx]
        ancestor = row.get("ancestor")
        date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else row["key"]
        description = f"{date_str}  {row['message']}"
        if row.get("presetname"):
            description += f"  ★ {row['presetname']}"
        try:
            if ancestor is not None:
                entry = ancestor.memory.get_memory(key=row["key"])
                sliced = self.memory.get_ancestor_recall_dict(
                    ancestor, entry, selection=None
                )
                self._load_mem(
                    {"settings": sliced}, key=None, path=None,
                    source_ancestor_name=row["ancestor_name"],
                    description=f"[{row['ancestor_name']}] {description}",
                )
                self.preset_tag_edit.setText("")
            else:
                mem = self.memory.get_memory(key=row["key"])
                self._load_mem(mem, key=row["key"], path=None, description=description)
                self.preset_tag_edit.setText(row.get("presetname") or "")
        except Exception as e:
            self._set_status(f"could not load memory: {e}", error=True)

    def _on_load_file(self):
        from qtpy import QtWidgets

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.window, "Load memory from file", "", _JSON_FILETYPES_QT
        )
        if not path:
            return
        try:
            mem = self.memory.get_memory(input_obj=path)
        except Exception as e:
            self._set_status(f"could not load {path}: {e}", error=True)
            return
        self._load_mem(mem, key=None, path=path, description=f"file {path}")
        self.preset_tag_edit.setText("")

    def _on_apply_preset_name(self):
        key = self._state.get("key")
        if key is None:
            self._set_status(
                "select a stored memory from the list above first "
                "(not a file-loaded or parent-sourced one)", error=True,
            )
            return
        name = self.preset_tag_edit.text().strip() or None
        try:
            self.memory.set_preset_name(key=key, name=name)
            self._set_status(f"preset tag {'set to ' + name if name else 'cleared'} for {key}")
            self._refresh_overview()
        except Exception as e:
            self._set_status(f"could not update preset tag: {e}", error=True)

    def _clear_group_checks(self):
        while self.group_row.count() > 1:
            child = self.group_row.takeAt(1)
            if child.widget():
                child.widget().deleteLater()
        self._group_checks = {}

    def _on_delete_entry(self):
        from qtpy import QtWidgets

        key = self._state.get("key")
        if key is None:
            self._set_status(
                "select a stored memory from the list above first "
                "(not a file-loaded or parent-sourced one)", error=True,
            )
            return
        reply = QtWidgets.QMessageBox.question(
            self.window,
            "Confirm delete",
            f"Permanently remove this stored memory entry ({key})? "
            "This cannot be undone from here.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.memory.clear_memory(key=key)
        except Exception as e:
            self._set_status(f"delete failed: {e}", error=True)
            return
        self._state = dict(_EMPTY_STATE, checked={})
        self.tree.clear()
        self._clear_group_checks()
        self.loaded_label.setText("<i>no memory loaded -- pick one from the list</i>")
        self.detail_count_label.setText("")
        self.preset_tag_edit.setText("")
        self._set_status(f"deleted stored memory {key}")
        self._refresh_overview()

    # --- detail / recall --------------------------------------------------

    def _load_mem(self, mem, key, path, source_ancestor_name=None, description=None):
        from qtpy import QtWidgets
        import html

        self._state = dict(
            _EMPTY_STATE, key=key, path=path, mem=mem, checked={},
            source_ancestor_name=source_ancestor_name,
        )
        groups = _available_recall_groups(self.memory, mem)
        # rebuild the group-filter checkboxes for this memory's own groups
        self._clear_group_checks()
        for g in groups:
            cb = QtWidgets.QCheckBox(g)
            cb.setChecked(True)
            cb.stateChanged.connect(self._refresh_detail)
            self.group_row.addWidget(cb)
            self._group_checks[g] = cb
        if description is None:
            description = f"file {path}" if path else f"memory {key}"
        self.loaded_label.setText(f"<b>loaded:</b> {html.escape(description)}")
        self.docks["detail"].raise_()  # in front of "Items to save" when tabbed
        if source_ancestor_name:
            self._set_status(
                f"loaded memory from parent '{source_ancestor_name}' "
                f"(only this object's own components)"
            )
        else:
            self._set_status(f"loaded {'file ' + path if path else 'memory ' + key}")
        self._refresh_detail()

    def _selected_selection(self):
        if not self._group_checks:
            return None
        checked = [g for g, cb in self._group_checks.items() if cb.isChecked()]
        if not checked or len(checked) == len(self._group_checks):
            return None
        return checked

    def _set_all_checked(self, value):
        """(Un)check every row currently shown -- hidden rows keep theirs."""
        for name in self._state.get("visible", []):
            self._state["checked"][name] = value
        self._render_detail()

    def _on_tree_item_changed(self, item, column):
        if self._suppress_item_changed or column != 0:
            return
        from qtpy import QtCore

        name = item.data(0, QtCore.Qt.UserRole)
        if name is None:
            return
        self._state["checked"][name] = item.checkState(0) == QtCore.Qt.Checked
        self._update_detail_count()

    def _make_detail_leaf(self, r, label):
        from qtpy import QtWidgets

        status = "not found" if not r["available"] else ("CHANGED" if r["changed"] else "==")
        present = _format_value(r["present"]) if r["available"] else "?"
        item = QtWidgets.QTreeWidgetItem(
            [label, present, _format_value(r["memory"]), r["diff"], status]
        )
        if r["changed"]:
            for col in range(5):
                item.setForeground(col, QtWidgets.QApplication.palette().link())
        return item

    @staticmethod
    def _detail_summary(rows):
        n_changed = sum(1 for r in rows if r["changed"])
        n_missing = sum(1 for r in rows if not r["available"])
        status = f"{n_changed} changed / {len(rows)}"
        if n_missing:
            status += f", {n_missing} not found"
        return ["", "", "", status]

    def _refresh_detail(self, *_a):
        """Re-read present values (hardware) for the loaded memory, then
        re-render. Filtering/searching only re-renders (`_render_detail`)."""
        mem = self._state.get("mem")
        if mem is None:
            self.tree.clear()
            return
        rows = _diff_rows(self.memory, mem, selection=self._selected_selection())
        self._state["rows"] = rows
        for r in rows:
            self._state["checked"].setdefault(r["name"], True)
        self._render_detail()

    def _render_detail(self, *_a):
        if self._state.get("mem") is None:
            self.tree.clear()
            return
        search = self.detail_search.text()
        visible = _visible_detail_rows(
            self._state.get("rows", []), changed_only=self.changed_only_cb.isChecked(), search=search,
        )
        self._state["visible"] = [r["name"] for r in visible]
        self._fill_check_tree(
            self.tree, _name_tree(visible), key=lambda r: r["name"],
            make_leaf=self._make_detail_leaf, summary=self._detail_summary,
            checked=self._state["checked"], expanded=self._detail_expanded,
            auto_expand=bool(search.strip()) or len(visible) <= _AUTO_EXPAND_MAX_ROWS,
        )
        self._update_detail_count()

    def _selected_detail_rows(self):
        visible = set(self._state.get("visible", []))
        checked = self._state["checked"]
        return [
            r for r in self._state.get("rows", [])
            if r["name"] in visible and checked.get(r["name"], True)
        ]

    def _update_detail_count(self):
        n_rows = len(self._state.get("rows", []))
        n_visible = len(self._state.get("visible", []))
        n_selected = len(self._selected_detail_rows())
        text = f"{n_selected} of {n_visible} shown selected"
        if n_rows > n_visible:
            text += f" ({n_rows - n_visible} hidden by filters, not recalled)"
        self.detail_count_label.setText(text)

    def _on_recall(self):
        from qtpy import QtWidgets

        if self._state.get("mem") is None:
            self._set_status("load a memory first", error=True)
            return
        selected_rows = self._selected_detail_rows()
        if not selected_rows:
            self._set_status("no shown rows selected to recall", error=True)
            return
        n_changed = sum(1 for r in selected_rows if r["changed"])
        reply = QtWidgets.QMessageBox.question(
            self.window,
            "Confirm recall",
            f"Recall {len(selected_rows)} selected value(s)? {n_changed} will actually change.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        filtered = {r["name"]: r["memory"] for r in selected_rows}
        try:
            self.memory.recall(input_obj={"settings": filtered}, force=True, selection="settings")
            recall_error = None
        except Exception as e:
            recall_error = e
        try:
            self._refresh_detail()
        except Exception:
            pass  # _diff_rows already guards each row's own read individually
        if recall_error is None:
            self._set_status("recall complete")
        else:
            self._set_status(_recall_error_status(recall_error), error=True)

    def _on_export(self):
        from qtpy import QtWidgets

        mem = self._state.get("mem")
        if mem is None:
            self._set_status("load a memory first", error=True)
            return
        default_name = _default_export_name(self.memory, _export_label(self._state))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.window, "Export memory to file", default_name, _JSON_FILETYPES_QT
        )
        if not path:
            return
        try:
            _export_to_file(mem, path)
            self._set_status(f"exported to {path}")
        except Exception as e:
            self._set_status(f"export failed: {e}", error=True)

    # --- save / items to save --------------------------------------------

    def _on_toggle_pick_items(self, _state):
        on = self.pick_items_cb.isChecked()
        self.docks["pick"].setVisible(on)
        if on:
            self.docks["pick"].raise_()
            self._refresh_pick_items()
        else:
            self._pick_pairs = []
            self._pick_visible = []
            self.pick_items_list.clear()
            self.pick_count_label.setText("")

    def _refresh_pick_items(self, *_a):
        """Re-read the candidates (a `get_status` over the selection), keeping
        the check state of names seen before; new names start checked."""
        if not self.pick_items_cb.isChecked():
            return
        selection = self.save_selection_combo.currentText().strip() or "settings"
        try:
            pairs = self.memory.get_memorize_candidates(selection=selection)
        except Exception as e:
            self._set_status(f"could not list items: {e}", error=True)
            return
        self._pick_pairs = pairs
        for _group, name, _value in pairs:
            self._pick_checked.setdefault(name, True)
        self._render_pick_items()

    def _render_pick_items(self, *_a):
        from qtpy import QtWidgets

        search = self.pick_search.text()
        visible = _visible_pick_items(self._pick_pairs, search)
        self._pick_visible = visible
        groups = []
        for p in visible:
            if p[0] not in groups:
                groups.append(p[0])
        name_of = lambda p: p[1]
        multi_group = len({p[0] for p in self._pick_pairs}) > 1
        self.pick_items_list.setColumnHidden(2, not multi_group)
        if multi_group:
            # several selection groups: one top-level fold per group
            root = {"label": "", "path": "", "item": None, "children": [
                {
                    "label": f"[{g}]", "path": f"[{g}]", "item": None,
                    "children": _name_tree([p for p in visible if p[0] == g], name=name_of)["children"],
                }
                for g in groups
            ]}
        else:
            root = _name_tree(visible, name=name_of)
        self._fill_check_tree(
            self.pick_items_list, root, key=name_of,
            make_leaf=lambda p, label: QtWidgets.QTreeWidgetItem([label, _format_value(p[2]), p[0]]),
            summary=lambda items: [f"{len(items)} items", ""],
            checked=self._pick_checked, expanded=self._pick_expanded,
            auto_expand=bool(search.strip()) or len(visible) <= _AUTO_EXPAND_MAX_ROWS,
        )
        self._update_pick_count()

    def _picked_names(self):
        return [p[1] for p in self._pick_visible if self._pick_checked.get(p[1], True)]

    def _update_pick_count(self):
        n_all = len(self._pick_pairs)
        n_visible = len(self._pick_visible)
        text = f"{len(self._picked_names())} of {n_visible} shown selected for saving"
        if n_all > n_visible:
            text += f" ({n_all - n_visible} hidden by the search, not saved)"
        self.pick_count_label.setText(text)

    def _on_pick_item_changed(self, item, column):
        if self._suppress_item_changed or column != 0:
            return
        from qtpy import QtCore

        name = item.data(0, QtCore.Qt.UserRole)
        if name is None:
            return
        self._pick_checked[name] = item.checkState(0) == QtCore.Qt.Checked
        self._update_pick_count()

    def _set_all_pick_items(self, value):
        for p in self._pick_visible:
            self._pick_checked[p[1]] = value
        self._render_pick_items()

    def _on_save(self):
        message = self.message_edit.text().strip()
        if not message:
            self._set_status("enter a message before saving", error=True)
            return
        selection = self.save_selection_combo.currentText().strip() or "settings"
        kwargs = {}
        if self.pick_items_cb.isChecked():
            names = self._picked_names()
            if not names:
                self._set_status("no items selected -- uncheck 'choose items' to save everything, or check at least one", error=True)
                return
            kwargs["pick_items"] = names
        preset_name = self.save_preset_edit.text().strip() or None
        try:
            self.memory.memorize(
                message=message, force_message=False, to_elog=self.elog_cb.isChecked(),
                selection=selection, preset_varname=preset_name, **kwargs,
            )
            self.message_edit.clear()
            self.save_preset_edit.clear()
            self._set_status("saved new memory")
            self._refresh_overview()
        except Exception as e:
            self._set_status(f"save failed: {e}\n{traceback.format_exc(limit=2)}", error=True)


def _descending():
    from qtpy import QtCore

    return QtCore.Qt.DescendingOrder


def make_memory_browser_qt(assembly, parent=None):
    return MemoryBrowserQt(assembly, parent=parent)


# ---------------------------------------------------------------------------
# ipywidgets backend
# ---------------------------------------------------------------------------


def _ipy_check_tree(root, key, make_leaf, checked, expanded, auto_expand, on_change=None):
    """ipywidgets rendering of a `_name_tree`: a leaf is a checkbox row
    (`make_leaf(item, label)` gives the widget beside the checkbox), a group
    an Accordion whose body starts with "all"/"none" buttons for everything
    below it. Check state lives in `checked` (name -> bool), fold state in
    `expanded` (group path -> bool). Returns a list of widgets."""
    import ipywidgets as widgets

    boxes = {}

    def _leaf(item, label):
        name = key(item)
        cb = widgets.Checkbox(
            value=checked.get(name, True), indent=False, layout=widgets.Layout(width="30px"),
        )

        def _on(change, name=name):
            checked[name] = change["new"]
            if on_change is not None:
                on_change()

        cb.observe(_on, names="value")
        boxes[name] = cb
        return widgets.HBox([cb, make_leaf(item, label)])

    def _set(names, value):
        for n in names:
            checked[n] = value
            if n in boxes:
                boxes[n].value = value

    def _node(node):
        if not node["children"]:
            return _leaf(node["item"], node["label"])
        names = [key(i) for i in _tree_items(node)]
        all_btn = widgets.Button(description="all", layout=widgets.Layout(width="50px"))
        none_btn = widgets.Button(description="none", layout=widgets.Layout(width="60px"))
        all_btn.on_click(lambda _b, ns=names: _set(ns, True))
        none_btn.on_click(lambda _b, ns=names: _set(ns, False))
        body = [widgets.HBox([all_btn, none_btn])]
        if node["item"] is not None:
            body.append(_leaf(node["item"], f"{node['label']} (own value)"))
        body += [_node(c) for c in node["children"]]
        acc = widgets.Accordion(children=[widgets.VBox(body)])
        acc.set_title(0, f"{node['label']} ({len(names)})")
        acc.selected_index = 0 if expanded.get(node["path"], auto_expand) else None

        def _on_fold(change, path=node["path"]):
            expanded[path] = change["new"] is not None

        acc.observe(_on_fold, names="selected_index")
        return acc

    return [_node(c) for c in root["children"]]


def _ipy_pop_out_button(section, title, status):
    """A "pop out" button showing `section` in a JupyterLab Sidecar panel
    (see `eco.widgets.jupyter_sidecar`) -- a second, live view of the same
    widgets; closing the panel leaves the one in the notebook untouched."""
    import ipywidgets as widgets

    btn = widgets.Button(description="pop out", tooltip="show this section in its own JupyterLab panel")
    panels = []

    def _pop(_b):
        try:
            from sidecar import Sidecar
            from IPython.display import display

            sc = Sidecar(title=title, anchor="split-right")
            with sc:
                display(section)
            panels.append(sc)  # garbage-collected Sidecars close themselves
        except Exception as e:
            status(f"pop out needs JupyterLab with the 'sidecar' package: {e}", error=True)

    btn.on_click(_pop)
    return btn


def make_memory_browser_ipywidgets(assembly):
    import ipywidgets as widgets

    memory = assembly.memory
    state = {
        "key": None, "path": None, "mem": None, "rows": [], "checked": {},
        "source_ancestor_name": None,
    }

    title = widgets.HTML(
        f"<b>Memories - {assembly.alias.get_full_name()}</b>"
    )

    overview_selection_combo = widgets.Combobox(
        value="settings",
        options=["all", "settings"],
        placeholder="settings",
        description="Selection:",
        ensure_option=False,
        layout=widgets.Layout(width="220px"),
    )
    show_parents_cb = widgets.Checkbox(value=False, description="show parent memories too")
    presets_only_cb = widgets.Checkbox(value=False, description="presets only", indent=False)
    overview_search = widgets.Text(
        placeholder="search message, preset name, group, date… (all words must match)",
        continuous_update=True, layout=widgets.Layout(width="60%"),
    )
    overview_count = widgets.Label("")
    # injected <style>: ancestor row colours, plus a user-resizable list
    overview_style_html = widgets.HTML("")
    overview_box = widgets.VBox([], layout=widgets.Layout(
        height="320px", overflow="auto", border="1px solid #ccc",
    ))
    overview_box.add_class("eco-mem-resizable")
    overview_rows = {"all": []}
    legend_html = widgets.HTML("")

    refresh_btn = widgets.Button(description="Refresh list")
    load_path_text = widgets.Text(
        placeholder="path to a memory .json file to load",
        layout=widgets.Layout(width="70%"),
    )
    load_file_btn = widgets.Button(description="Load from file")

    preset_tag_text = widgets.Text(
        placeholder="preset name for the loaded entry (empty clears it)",
        layout=widgets.Layout(width="50%"),
    )
    apply_preset_btn = widgets.Button(description="Apply preset tag")
    delete_entry_btn = widgets.Button(
        description="Delete entry", button_style="danger",
        tooltip=(
            "permanently remove this stored memory entry (not the whole "
            "device's memory -- just this one loaded snapshot)"
        ),
    )
    _delete_armed = {"key": None}

    group_filter_box = widgets.HBox([widgets.Label("Keyword groups:")])
    changed_only_cb = widgets.Checkbox(value=True, description="show changed only")
    detail_search = widgets.Text(
        placeholder="search item names… (e.g. \"mono energy\")", continuous_update=True,
        layout=widgets.Layout(width="50%"),
    )
    loaded_html = widgets.HTML("<i>no memory loaded -- pick one from the list</i>")
    detail_count = widgets.Label("")
    detail_expanded = {}

    detail_box = widgets.VBox([], layout=widgets.Layout(
        height="420px", overflow="auto", border="1px solid #ccc",
    ))
    detail_box.add_class("eco-mem-resizable")
    select_all_btn = widgets.Button(description="select shown")
    select_none_btn = widgets.Button(description="unselect shown")
    status_html = widgets.HTML("")

    recall_btn = widgets.Button(description="Recall selected values", button_style="warning")
    export_path_text = widgets.Text(
        placeholder="path to export the loaded memory to",
        layout=widgets.Layout(width="70%"),
    )
    export_btn = widgets.Button(description="Export to file")

    message_text = widgets.Text(
        placeholder="message for this memory…", layout=widgets.Layout(width="60%")
    )
    save_selection_combo = widgets.Combobox(
        value="settings",
        options=["all", "settings"],
        placeholder="settings",
        description="selection:",
        ensure_option=False,
        layout=widgets.Layout(width="220px"),
        tooltip=(
            'which status_collection selection to capture -- "settings" '
            '(default), "all" (everything currently registered), or a '
            "custom tag"
        ),
    )
    elog_cb = widgets.Checkbox(value=True, description="post to elog")
    save_btn = widgets.Button(description="Save new memory", button_style="success")
    save_preset_text = widgets.Text(
        placeholder="preset name (optional) -- leave empty for a plain memory",
        layout=widgets.Layout(width="60%"),
        tooltip=(
            "tag this memory as a named preset, reachable afterwards as "
            "<assembly>.memory.presets.<name>(). Reusing an existing name "
            "redirects that preset to this new entry; the old entry stays "
            "around as a plain memory."
        ),
    )

    # opt-in, non-default item picker: unchecked, this box stays empty and
    # Save captures everything the selection resolves to, exactly as
    # before this existed.
    pick_items_cb = widgets.Checkbox(
        value=False, description="choose items…",
        tooltip=(
            "narrow this save down to hand-picked items instead of "
            "capturing everything the selection above resolves to"
        ),
    )
    refresh_items_btn = widgets.Button(description="Refresh items")
    pick_all_items_btn = widgets.Button(description="all", layout=widgets.Layout(width="50px"))
    pick_none_items_btn = widgets.Button(description="none", layout=widgets.Layout(width="60px"))
    items_pick_box = widgets.VBox([], layout=widgets.Layout(overflow="auto"))
    items_pick_box.add_class("eco-mem-resizable")
    pick_search = widgets.Text(
        placeholder="search item names…", continuous_update=True,
        layout=widgets.Layout(width="40%"),
    )
    pick_count = widgets.Label("")
    pick_state = {"pairs": [], "visible": [], "checked": {}, "expanded": {}}

    group_checks = {}

    def _set_status(text, error=False):
        color = "#c62828" if error else "#2e7d32"
        status_html.value = f"<span style='color:{color}'>{text}</span>"

    def _reset_delete_arm():
        _delete_armed["key"] = None
        delete_entry_btn.description = "Delete entry"

    def _load_row(row):
        import html

        ancestor = row.get("ancestor")
        date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else row["key"]
        desc = f"{date_str}  {row['message']}"
        if row.get("presetname"):
            desc += f"  ★ {row['presetname']}"
        if ancestor is not None:
            desc = f"[{row['ancestor_name']}] {desc}"
        loaded_html.value = f"<b>loaded:</b> {html.escape(desc)}"
        try:
            if ancestor is not None:
                entry = ancestor.memory.get_memory(key=row["key"])
                sliced = memory.get_ancestor_recall_dict(ancestor, entry, selection=None)
                _load_mem(
                    {"settings": sliced}, key=None, path=None,
                    source_ancestor_name=row["ancestor_name"],
                )
                preset_tag_text.value = ""
            else:
                mem = memory.get_memory(key=row["key"])
                _load_mem(mem, key=row["key"], path=None)
                preset_tag_text.value = row.get("presetname") or ""
            _reset_delete_arm()
        except Exception as e:
            _set_status(f"could not load memory: {e}", error=True)

    def _on_apply_preset_name(_b):
        key = state.get("key")
        if key is None:
            _set_status(
                "select a stored memory from the list above first "
                "(not a file-loaded or parent-sourced one)", error=True,
            )
            return
        name = preset_tag_text.value.strip() or None
        try:
            memory.set_preset_name(key=key, name=name)
            _set_status(f"preset tag {'set to ' + name if name else 'cleared'} for {key}")
            _refresh_overview()
        except Exception as e:
            _set_status(f"could not update preset tag: {e}", error=True)

    def _on_delete_entry(_b):
        key = state.get("key")
        if key is None:
            _set_status(
                "select a stored memory from the list above first "
                "(not a file-loaded or parent-sourced one)", error=True,
            )
            return
        if _delete_armed["key"] != key:
            _delete_armed["key"] = key
            delete_entry_btn.description = "Click again to confirm delete"
            _set_status(f"click 'Delete entry' again to permanently remove {key}", error=True)
            return
        try:
            memory.clear_memory(key=key)
        except Exception as e:
            _set_status(f"delete failed: {e}", error=True)
            return
        state.update({
            "key": None, "path": None, "mem": None, "checked": {},
            "source_ancestor_name": None,
        })
        state["rows"] = []
        state["visible"] = []
        detail_box.children = ()
        detail_count.value = ""
        loaded_html.value = "<i>no memory loaded -- pick one from the list</i>"
        group_filter_box.children = (widgets.Label("Keyword groups:"),)
        group_checks.clear()
        preset_tag_text.value = ""
        _reset_delete_arm()
        _set_status(f"deleted stored memory {key}")
        _refresh_overview()

    def _refresh_overview(_b=None):
        own_rows = _overview_rows(memory)
        all_rows = list(own_rows)
        if show_parents_cb.value:
            all_rows += _ancestor_overview_rows(memory)
        overview_rows["all"] = all_rows
        overview_selection_combo.options = ["all"] + _known_selection_tags(all_rows)
        _render_overview()

    def _render_overview(_change=None):
        all_rows = overview_rows["all"]
        tag = overview_selection_combo.value or "settings"
        rows = _filter_overview_rows(all_rows, tag)
        if presets_only_cb.value:
            rows = [r for r in rows if r.get("presetname")]
        rows = [r for r in rows if _overview_row_matches(r, overview_search.value)]
        overview_count.value = f"{len(rows)} of {len(all_rows)} shown"

        ancestor_names = [r["ancestor_name"] for r in rows if "ancestor_name" in r]
        color_map = _ancestor_color_map(ancestor_names)
        style_rules = []
        row_widgets = []
        for row in rows:
            date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "?"
            groups = ",".join(row["groups"])
            ancestor_name = row.get("ancestor_name")
            label = f"{date_str}   {row['message']}   [{groups}]"
            if row.get("presetname"):
                label += f"   ★ {row['presetname']}"
            if ancestor_name:
                label = f"[{ancestor_name}] {label}"
            btn = widgets.Button(
                description=label,
                layout=widgets.Layout(width="100%"),
            )
            if ancestor_name:
                cls = "eco-mem-anc-" + str(abs(hash(ancestor_name)) % 1000000)
                btn.add_class(cls)
                style_rules.append(
                    f".{cls} {{ background-color: {color_map[ancestor_name]} !important; }}"
                )
            btn.on_click(lambda _b, r=row: _load_row(r))
            row_widgets.append(btn)
        overview_box.children = tuple(row_widgets) if row_widgets else (
            widgets.Label("(no stored memories)"),
        )
        style_rules.append(".eco-mem-resizable { resize: vertical; }")
        overview_style_html.value = "<style>" + "".join(style_rules) + "</style>"
        if color_map:
            legend_html.value = "Parent memory colours: " + "; ".join(
                f'<span style="background-color:{c};">&nbsp;{n}&nbsp;</span>'
                for n, c in color_map.items()
            )
        else:
            legend_html.value = ""

    def _detail_label(r, label):
        if not r["available"]:
            status = "not found"
        elif r["changed"]:
            status = "<b style='color:#c62828'>CHANGED</b>"
        else:
            status = "=="
        present = _format_value(r["present"]) if r["available"] else "?"
        return widgets.HTML(
            f"<span title='{r['name']}'>{label}</span> &nbsp; present: <b>{present}</b> &nbsp; "
            f"memory: <b>{_format_value(r['memory'])}</b> &nbsp; diff: {r['diff']} "
            f"&nbsp; {status}"
        )

    def _selected_detail_rows():
        visible = set(state.get("visible", []))
        return [
            r for r in state.get("rows", [])
            if r["name"] in visible and state["checked"].get(r["name"], True)
        ]

    def _update_detail_count():
        n_rows, n_vis = len(state.get("rows", [])), len(state.get("visible", []))
        text = f"{len(_selected_detail_rows())} of {n_vis} shown selected"
        if n_rows > n_vis:
            text += f" ({n_rows - n_vis} hidden by filters, not recalled)"
        detail_count.value = text

    def _refresh_detail(_change=None):
        mem = state.get("mem")
        if mem is None:
            detail_box.children = ()
            return
        checked_groups = [g for g, cb in group_checks.items() if cb.value]
        selection = (
            None
            if (not checked_groups or len(checked_groups) == len(group_checks))
            else checked_groups
        )
        rows = _diff_rows(memory, mem, selection=selection)
        state["rows"] = rows
        for r in rows:
            state["checked"].setdefault(r["name"], True)
        _render_detail()

    def _render_detail(_change=None):
        if state.get("mem") is None:
            detail_box.children = ()
            return
        search = detail_search.value
        visible = _visible_detail_rows(
            state.get("rows", []), changed_only=changed_only_cb.value, search=search,
        )
        state["visible"] = [r["name"] for r in visible]
        sections = _ipy_check_tree(
            _name_tree(visible), key=lambda r: r["name"], make_leaf=_detail_label,
            checked=state["checked"], expanded=detail_expanded,
            auto_expand=bool(search.strip()) or len(visible) <= _AUTO_EXPAND_MAX_ROWS,
            on_change=_update_detail_count,
        )
        detail_box.children = tuple(sections) if sections else (widgets.HTML("(no rows)"),)
        _update_detail_count()

    def _load_mem(mem, key, path, source_ancestor_name=None):
        state.update({
            "key": key, "path": path, "mem": mem, "checked": {},
            "source_ancestor_name": source_ancestor_name,
        })
        groups = _available_recall_groups(memory, mem)
        group_checks.clear()
        boxes = [widgets.Label("Keyword groups:")]
        for g in groups:
            cb = widgets.Checkbox(value=True, description=g, indent=False,
                                   layout=widgets.Layout(width="140px"))
            cb.observe(_refresh_detail, names="value")
            group_checks[g] = cb
            boxes.append(cb)
        group_filter_box.children = tuple(boxes)
        if source_ancestor_name:
            _set_status(
                f"loaded memory from parent '{source_ancestor_name}' "
                f"(only this object's own components)"
            )
        else:
            _set_status(f"loaded {'file ' + path if path else 'memory ' + key}")
        _refresh_detail()

    def _on_load_file(_b):
        path = load_path_text.value.strip()
        if not path:
            _set_status("enter a file path to load", error=True)
            return
        try:
            mem = memory.get_memory(input_obj=path)
        except Exception as e:
            _set_status(f"could not load {path}: {e}", error=True)
            return
        _load_mem(mem, key=None, path=path)
        loaded_html.value = f"<b>loaded:</b> file {path}"
        preset_tag_text.value = ""
        _reset_delete_arm()

    def _select_all(_b):
        for name in state.get("visible", []):
            state["checked"][name] = True
        _render_detail()

    def _select_none(_b):
        for name in state.get("visible", []):
            state["checked"][name] = False
        _render_detail()

    def _on_recall(_b):
        if state.get("mem") is None:
            _set_status("load a memory first", error=True)
            return
        selected_rows = _selected_detail_rows()
        if not selected_rows:
            _set_status("no shown rows selected to recall", error=True)
            return
        filtered = {r["name"]: r["memory"] for r in selected_rows}
        try:
            memory.recall(input_obj={"settings": filtered}, force=True, selection="settings")
            recall_error = None
        except Exception as e:
            recall_error = e
        try:
            _refresh_detail()
        except Exception:
            pass  # _diff_rows already guards each row's own read individually
        if recall_error is None:
            _set_status("recall complete")
        else:
            _set_status(_recall_error_status(recall_error), error=True)

    def _on_export(_b):
        mem = state.get("mem")
        if mem is None:
            _set_status("load a memory first", error=True)
            return
        path = export_path_text.value.strip() or _default_export_name(memory, _export_label(state))
        try:
            _export_to_file(mem, path)
            _set_status(f"exported to {path}")
        except Exception as e:
            _set_status(f"export failed: {e}", error=True)

    def _picked_names():
        return [p[1] for p in pick_state["visible"] if pick_state["checked"].get(p[1], True)]

    def _update_pick_count():
        n_all, n_vis = len(pick_state["pairs"]), len(pick_state["visible"])
        text = f"{len(_picked_names())} of {n_vis} shown selected for saving"
        if n_all > n_vis:
            text += f" ({n_all - n_vis} hidden by the search, not saved)"
        pick_count.value = text

    def _refresh_pick_items(_b=None):
        if not pick_items_cb.value:
            return
        selection = save_selection_combo.value.strip() or "settings"
        try:
            pairs = memory.get_memorize_candidates(selection=selection)
        except Exception as e:
            _set_status(f"could not list items: {e}", error=True)
            return
        pick_state["pairs"] = pairs
        for _g, n, _v in pairs:
            pick_state["checked"].setdefault(n, True)
        _render_pick_items()

    def _render_pick_items(_change=None):
        if not pick_items_cb.value:
            return
        pairs = pick_state["pairs"]
        visible = _visible_pick_items(pairs, pick_search.value)
        pick_state["visible"] = visible
        name_of = lambda p: p[1]
        groups = []
        for p in visible:
            if p[0] not in groups:
                groups.append(p[0])
        if len({p[0] for p in pairs}) > 1:
            root = {"label": "", "path": "", "item": None, "children": [
                {
                    "label": f"[{g}]", "path": f"[{g}]", "item": None,
                    "children": _name_tree([p for p in visible if p[0] == g], name=name_of)["children"],
                }
                for g in groups
            ]}
        else:
            root = _name_tree(visible, name=name_of)
        children = _ipy_check_tree(
            root, key=name_of,
            make_leaf=lambda p, label: widgets.HTML(
                f"<span title='{p[1]}'>{label}</span> &nbsp; = &nbsp; {_format_value(p[2])}"
            ),
            checked=pick_state["checked"], expanded=pick_state["expanded"],
            auto_expand=bool(pick_search.value.strip()) or len(visible) <= _AUTO_EXPAND_MAX_ROWS,
            on_change=_update_pick_count,
        )
        items_pick_box.children = tuple(children) or (widgets.Label("(no items)"),)
        items_pick_box.layout.height = "360px"
        _update_pick_count()

    def _on_toggle_pick_items(change):
        if change.get("name") != "value":
            return
        if change["new"]:
            _refresh_pick_items()
        else:
            pick_state["pairs"] = []
            pick_state["visible"] = []
            items_pick_box.children = ()
            items_pick_box.layout.height = None
            pick_count.value = ""

    def _set_all_pick_items(value):
        for p in pick_state["visible"]:
            pick_state["checked"][p[1]] = value
        _render_pick_items()

    def _on_save(_b):
        message = message_text.value.strip()
        if not message:
            _set_status("enter a message before saving", error=True)
            return
        selection = save_selection_combo.value.strip() or "settings"
        kwargs = {}
        if pick_items_cb.value:
            names = _picked_names()
            if not names:
                _set_status(
                    "no items selected -- uncheck 'choose items' to save "
                    "everything, or check at least one", error=True,
                )
                return
            kwargs["pick_items"] = names
        preset_name = save_preset_text.value.strip() or None
        try:
            memory.memorize(
                message=message, force_message=False, to_elog=elog_cb.value,
                selection=selection, preset_varname=preset_name, **kwargs,
            )
            message_text.value = ""
            save_preset_text.value = ""
            _set_status("saved new memory")
            _refresh_overview()
        except Exception as e:
            _set_status(f"save failed: {e}", error=True)

    overview_selection_combo.observe(_render_overview, names="value")
    presets_only_cb.observe(_render_overview, names="value")
    overview_search.observe(_render_overview, names="value")
    detail_search.observe(_render_detail, names="value")
    pick_search.observe(_render_pick_items, names="value")
    show_parents_cb.observe(_refresh_overview, names="value")
    refresh_btn.on_click(_refresh_overview)
    load_file_btn.on_click(_on_load_file)
    apply_preset_btn.on_click(_on_apply_preset_name)
    delete_entry_btn.on_click(_on_delete_entry)
    changed_only_cb.observe(_render_detail, names="value")
    select_all_btn.on_click(_select_all)
    select_none_btn.on_click(_select_none)
    recall_btn.on_click(_on_recall)
    export_btn.on_click(_on_export)
    save_btn.on_click(_on_save)
    pick_items_cb.observe(_on_toggle_pick_items, names="value")
    refresh_items_btn.on_click(_refresh_pick_items)
    pick_all_items_btn.on_click(lambda _b: _set_all_pick_items(True))
    pick_none_items_btn.on_click(lambda _b: _set_all_pick_items(False))

    _refresh_overview()

    pick_all_items_btn.description = "all shown"
    pick_all_items_btn.layout.width = "90px"
    pick_none_items_btn.description = "none shown"
    pick_none_items_btn.layout.width = "100px"

    overview_section = widgets.VBox([
        widgets.HTML("<b>Stored memories</b>"),
        widgets.HBox([overview_selection_combo, show_parents_cb, presets_only_cb]),
        widgets.HBox([overview_search, overview_count]),
        overview_style_html,
        overview_box,
        legend_html,
        widgets.HBox([refresh_btn, load_path_text, load_file_btn]),
        widgets.HBox([preset_tag_text, apply_preset_btn, delete_entry_btn]),
    ])
    detail_section = widgets.VBox([
        widgets.HTML("<b>Loaded memory - recall</b> (checkbox selects)"),
        loaded_html,
        group_filter_box,
        widgets.HBox([changed_only_cb, detail_search]),
        detail_box,
        widgets.HBox([select_all_btn, select_none_btn, detail_count]),
        widgets.HBox([recall_btn, export_path_text, export_btn]),
    ])
    save_section = widgets.VBox([
        widgets.HTML("<b>Save current state as a new memory</b>"),
        widgets.HBox([message_text, save_selection_combo, elog_cb, save_btn]),
        widgets.HBox([save_preset_text]),
        widgets.HBox([
            pick_items_cb, pick_search, refresh_items_btn, pick_all_items_btn, pick_none_items_btn,
        ]),
        items_pick_box,
        pick_count,
    ])
    name = assembly.alias.get_full_name()
    box = widgets.VBox(
        [
            title,
            widgets.HBox([
                _ipy_pop_out_button(overview_section, f"memories: {name}", _set_status),
            ]),
            overview_section,
            _ipy_pop_out_button(detail_section, f"recall: {name}", _set_status),
            detail_section,
            _ipy_pop_out_button(save_section, f"save: {name}", _set_status),
            save_section,
            status_html,
        ]
    )
    return box


# ---------------------------------------------------------------------------
# Tk backend
# ---------------------------------------------------------------------------


class MemoryBrowserTk:
    def __init__(self, assembly, parent=None):
        self.assembly = assembly
        self.memory = assembly.memory
        self.parent = parent
        self.top = None
        self._state = {
            "key": None, "path": None, "mem": None, "rows": [], "checked": {},
            "source_ancestor_name": None,
        }
        self._group_vars = {}
        self._build()

    def _build(self):
        import tkinter as tk
        from tkinter import ttk

        self.top = tk.Toplevel(self.parent) if self.parent is not None else tk.Tk()
        self.top.title(f"Memories - {self.assembly.alias.get_full_name()}")

        ttk.Label(self.top, text="Stored memories", font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w", padx=4, pady=(4, 0)
        )
        overview_filter_row = ttk.Frame(self.top)
        overview_filter_row.pack(fill="x", padx=4)
        ttk.Label(overview_filter_row, text="Selection:").pack(side="left")
        self.overview_selection_var = tk.StringVar(value="settings")
        self.overview_selection_combo = ttk.Combobox(
            overview_filter_row, textvariable=self.overview_selection_var,
            values=["all", "settings"], width=20,
        )
        self.overview_selection_combo.pack(side="left", padx=(4, 0))
        self.overview_selection_combo.bind(
            "<<ComboboxSelected>>", lambda _evt: self._render_overview()
        )
        self.overview_selection_combo.bind(
            "<Return>", lambda _evt: self._render_overview()
        )
        self.show_parents_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            overview_filter_row, text="show parent memories too",
            variable=self.show_parents_var, command=self._refresh_overview,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(overview_filter_row, text="search:").pack(side="left", padx=(12, 0))
        self.overview_search_var = tk.StringVar()
        self.overview_search_var.trace_add("write", lambda *_a: self._render_overview())
        ttk.Entry(overview_filter_row, textvariable=self.overview_search_var, width=30).pack(
            side="left", padx=(4, 0), fill="x", expand=True
        )

        list_frame = ttk.Frame(self.top)
        list_frame.pack(fill="x", padx=4)
        self.overview_list = tk.Listbox(list_frame, height=14)
        self.overview_list.pack(side="left", fill="x", expand=True)
        self.overview_list.bind("<<ListboxSelect>>", self._on_pick_stored)
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.overview_list.yview)
        vsb.pack(side="right", fill="y")
        self.overview_list.config(yscrollcommand=vsb.set)

        self.legend_label = ttk.Label(self.top, text="", wraplength=680)
        self.legend_label.pack(anchor="w", padx=4)

        load_row = ttk.Frame(self.top)
        load_row.pack(fill="x", padx=4, pady=2)
        ttk.Button(load_row, text="Refresh list", command=self._refresh_overview).pack(
            side="left"
        )
        ttk.Button(load_row, text="Load from file…", command=self._on_load_file).pack(
            side="left", padx=(4, 0)
        )

        preset_tag_row = ttk.Frame(self.top)
        preset_tag_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(preset_tag_row, text="Preset tag (for loaded entry):").pack(side="left")
        self.preset_tag_var = tk.StringVar()
        ttk.Entry(preset_tag_row, textvariable=self.preset_tag_var, width=20).pack(
            side="left", padx=(4, 4)
        )
        ttk.Button(
            preset_tag_row, text="Apply", command=self._on_apply_preset_name
        ).pack(side="left")
        ttk.Button(
            preset_tag_row, text="Delete entry", command=self._on_delete_entry
        ).pack(side="left", padx=(8, 0))

        filter_box = ttk.LabelFrame(self.top, text="Filters (within an opened memory)")
        filter_box.pack(fill="x", padx=4, pady=4)
        self.group_row = ttk.Frame(filter_box)
        self.group_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(self.group_row, text="Keyword groups:").pack(side="left")
        self.changed_only_var = tk.BooleanVar(value=True)
        changed_row = ttk.Frame(filter_box)
        changed_row.pack(fill="x", padx=4)
        ttk.Checkbutton(
            changed_row, text="show changed only", variable=self.changed_only_var,
            command=self._render_detail,
        ).pack(side="left")
        ttk.Label(changed_row, text="search names:").pack(side="left", padx=(12, 0))
        self.detail_search_var = tk.StringVar()
        self.detail_search_var.trace_add("write", lambda *_a: self._render_detail())
        ttk.Entry(changed_row, textvariable=self.detail_search_var, width=30).pack(
            side="left", padx=(4, 0), fill="x", expand=True
        )

        ttk.Label(
            self.top, text="Details (checkbox selects for recall)",
            font=("TkDefaultFont", 9, "bold"),
        ).pack(anchor="w", padx=4, pady=(4, 0))

        detail_frame = ttk.Frame(self.top)
        detail_frame.pack(fill="both", expand=True, padx=4)
        detail_canvas = tk.Canvas(detail_frame, highlightthickness=0, height=260)
        detail_vsb = ttk.Scrollbar(detail_frame, orient="vertical", command=detail_canvas.yview)
        self.detail_inner = ttk.Frame(detail_canvas)
        detail_inner_id = detail_canvas.create_window((0, 0), window=self.detail_inner, anchor="nw")

        def _on_inner_configure(_evt=None):
            detail_canvas.configure(scrollregion=detail_canvas.bbox("all"))

        def _on_canvas_configure(evt):
            detail_canvas.itemconfig(detail_inner_id, width=evt.width)

        self.detail_inner.bind("<Configure>", _on_inner_configure)
        detail_canvas.bind("<Configure>", _on_canvas_configure)
        detail_canvas.configure(yscrollcommand=detail_vsb.set)
        detail_canvas.pack(side="left", fill="both", expand=True)
        detail_vsb.pack(side="right", fill="y")

        select_row = ttk.Frame(self.top)
        select_row.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Button(select_row, text="select shown", command=lambda: self._set_all_checked(True)).pack(
            side="left"
        )
        ttk.Button(
            select_row, text="unselect shown", command=lambda: self._set_all_checked(False)
        ).pack(side="left", padx=(4, 0))
        self.detail_count_label = ttk.Label(select_row, text="")
        self.detail_count_label.pack(side="left", padx=(8, 0))

        action_row = ttk.Frame(self.top)
        action_row.pack(fill="x", padx=4, pady=4)
        ttk.Button(action_row, text="Recall selected values", command=self._on_recall).pack(
            side="left"
        )
        ttk.Button(action_row, text="Export to file…", command=self._on_export).pack(
            side="left", padx=(4, 0)
        )

        save_box = ttk.LabelFrame(self.top, text="Save current state as a new memory")
        save_box.pack(fill="x", padx=4, pady=4)
        self.message_var = tk.StringVar()
        ttk.Entry(save_box, textvariable=self.message_var, width=50).pack(
            side="left", padx=4, pady=4
        )
        ttk.Label(save_box, text="selection:").pack(side="left")
        self.save_selection_var = tk.StringVar(value="settings")
        ttk.Combobox(
            save_box, textvariable=self.save_selection_var,
            values=["all", "settings"], width=16,
        ).pack(side="left", padx=(4, 4))
        self.elog_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(save_box, text="post to elog", variable=self.elog_var).pack(
            side="left", padx=4
        )
        ttk.Label(save_box, text="preset:").pack(side="left", padx=(8, 0))
        self.save_preset_var = tk.StringVar()
        ttk.Entry(save_box, textvariable=self.save_preset_var, width=16).pack(
            side="left", padx=(2, 4)
        )
        ttk.Button(save_box, text="Save", command=self._on_save).pack(side="left", padx=4)

        self.status_label = ttk.Label(self.top, text="")
        self.status_label.pack(anchor="w", padx=4, pady=(0, 4))

        self.top.protocol("WM_DELETE_WINDOW", self.top.destroy)
        self._refresh_overview()

    def _set_status(self, text, error=False):
        self.status_label.config(text=text, foreground="#c62828" if error else "#2e7d32")

    def _refresh_overview(self):
        own_rows = _overview_rows(self.memory)
        all_rows = list(own_rows)
        if self.show_parents_var.get():
            all_rows += _ancestor_overview_rows(self.memory)
        self._overview_all = all_rows
        self.overview_selection_combo["values"] = ["all"] + _known_selection_tags(all_rows)
        self._render_overview()

    def _render_overview(self):
        tag = self.overview_selection_var.get() or "settings"
        search = self.overview_search_var.get()
        self._overview = [
            r for r in _filter_overview_rows(getattr(self, "_overview_all", []), tag)
            if _overview_row_matches(r, search)
        ]
        ancestor_names = [r["ancestor_name"] for r in self._overview if "ancestor_name" in r]
        color_map = _ancestor_color_map(ancestor_names)
        self.overview_list.delete(0, "end")
        for i, row in enumerate(self._overview):
            date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "?"
            groups = ",".join(row["groups"])
            ancestor_name = row.get("ancestor_name")
            label = f"{date_str}   {row['message']}   [{groups}]"
            if row.get("presetname"):
                label += f"   ★ {row['presetname']}"
            if ancestor_name:
                label = f"[{ancestor_name}] {label}"
            self.overview_list.insert("end", label)
            if ancestor_name:
                self.overview_list.itemconfig(i, {"bg": color_map[ancestor_name]})
        if color_map:
            self.legend_label.config(
                text="Parent memory colours: " + "; ".join(
                    f"{n} = {c}" for n, c in color_map.items()
                )
            )
        else:
            self.legend_label.config(text="")

    def _on_pick_stored(self, _evt=None):
        sel = self.overview_list.curselection()
        if not sel:
            return
        row = self._overview[sel[0]]
        ancestor = row.get("ancestor")
        try:
            if ancestor is not None:
                entry = ancestor.memory.get_memory(key=row["key"])
                sliced = self.memory.get_ancestor_recall_dict(
                    ancestor, entry, selection=None
                )
                self._load_mem(
                    {"settings": sliced}, key=None, path=None,
                    source_ancestor_name=row["ancestor_name"],
                )
                self.preset_tag_var.set("")
            else:
                mem = self.memory.get_memory(key=row["key"])
                self._load_mem(mem, key=row["key"], path=None)
                self.preset_tag_var.set(row.get("presetname") or "")
        except Exception as e:
            self._set_status(f"could not load memory: {e}", error=True)

    def _on_load_file(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self.top, title="Load memory from file", filetypes=_JSON_FILETYPES_TK
        )
        if not path:
            return
        try:
            mem = self.memory.get_memory(input_obj=path)
        except Exception as e:
            self._set_status(f"could not load {path}: {e}", error=True)
            return
        self._load_mem(mem, key=None, path=path)
        self.preset_tag_var.set("")

    def _on_apply_preset_name(self):
        key = self._state.get("key")
        if key is None:
            self._set_status(
                "select a stored memory from the list above first "
                "(not a file-loaded or parent-sourced one)", error=True,
            )
            return
        name = self.preset_tag_var.get().strip() or None
        try:
            self.memory.set_preset_name(key=key, name=name)
            self._set_status(f"preset tag {'set to ' + name if name else 'cleared'} for {key}")
            self._refresh_overview()
        except Exception as e:
            self._set_status(f"could not update preset tag: {e}", error=True)

    def _on_delete_entry(self):
        from tkinter import messagebox

        key = self._state.get("key")
        if key is None:
            self._set_status(
                "select a stored memory from the list above first "
                "(not a file-loaded or parent-sourced one)", error=True,
            )
            return
        if not messagebox.askyesno(
            "Confirm delete",
            f"Permanently remove this stored memory entry ({key})? "
            "This cannot be undone from here.",
            parent=self.top,
        ):
            return
        try:
            self.memory.clear_memory(key=key)
        except Exception as e:
            self._set_status(f"delete failed: {e}", error=True)
            return
        self._state = {
            "key": None, "path": None, "mem": None, "rows": [], "checked": {},
            "source_ancestor_name": None,
        }
        for child in self.detail_inner.winfo_children():
            child.destroy()
        for child in self.group_row.winfo_children()[1:]:
            child.destroy()
        self._group_vars = {}
        self.preset_tag_var.set("")
        self._set_status(f"deleted stored memory {key}")
        self._refresh_overview()

    def _load_mem(self, mem, key, path, source_ancestor_name=None):
        import tkinter as tk
        from tkinter import ttk

        self._state = {
            "key": key, "path": path, "mem": mem, "rows": [], "checked": {},
            "source_ancestor_name": source_ancestor_name,
        }
        for child in self.group_row.winfo_children()[1:]:
            child.destroy()
        self._group_vars = {}
        for g in _available_recall_groups(self.memory, mem):
            var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(
                self.group_row, text=g, variable=var, command=self._refresh_detail
            )
            cb.pack(side="left", padx=(4, 0))
            self._group_vars[g] = var
        if source_ancestor_name:
            self._set_status(
                f"loaded memory from parent '{source_ancestor_name}' "
                f"(only this object's own components)"
            )
        else:
            self._set_status(f"loaded {'file ' + path if path else 'memory ' + key}")
        self._refresh_detail()

    def _selected_selection(self):
        if not self._group_vars:
            return None
        checked = [g for g, v in self._group_vars.items() if v.get()]
        if not checked or len(checked) == len(self._group_vars):
            return None
        return checked

    def _set_all_checked(self, value):
        for name in self._state.get("visible", []):
            self._state["checked"][name] = value
        self._render_detail()

    def _selected_detail_rows(self):
        visible = set(self._state.get("visible", []))
        return [
            r for r in self._state.get("rows", [])
            if r["name"] in visible and self._state["checked"].get(r["name"], True)
        ]

    def _update_detail_count(self):
        n_rows, n_vis = len(self._state.get("rows", [])), len(self._state.get("visible", []))
        text = f"{len(self._selected_detail_rows())} of {n_vis} shown selected"
        if n_rows > n_vis:
            text += f" ({n_rows - n_vis} hidden by filters, not recalled)"
        self.detail_count_label.config(text=text)

    def _add_detail_row(self, r, label=None, depth=0):
        import tkinter as tk
        from tkinter import ttk

        row_frame = ttk.Frame(self.detail_inner)
        row_frame.pack(fill="x", anchor="w", padx=(16 * depth, 0))
        var = tk.BooleanVar(value=self._state["checked"].get(r["name"], True))

        def _on_toggle(name=r["name"], v=var):
            self._state["checked"][name] = v.get()
            self._update_detail_count()

        ttk.Checkbutton(row_frame, variable=var, command=_on_toggle).pack(side="left")
        status = "not found" if not r["available"] else ("CHANGED" if r["changed"] else "==")
        present = _format_value(r["present"]) if r["available"] else "?"
        text = (
            f"{label or r['name']}   present: {present}   memory: {_format_value(r['memory'])}   "
            f"diff: {r['diff']}   {status}"
        )
        lbl = ttk.Label(row_frame, text=text)
        if r["changed"]:
            lbl.configure(foreground="#1565c0")
        lbl.pack(side="left")

    def _refresh_detail(self):
        mem = self._state.get("mem")
        if mem is None:
            for child in self.detail_inner.winfo_children():
                child.destroy()
            return
        rows = _diff_rows(self.memory, mem, selection=self._selected_selection())
        self._state["rows"] = rows
        for r in rows:
            self._state["checked"].setdefault(r["name"], True)
        self._render_detail()

    def _render_detail(self):
        """Indented hierarchy of the shown rows (`_name_tree`); unlike Qt
        and ipywidgets, Tk groups are headers only -- not foldable."""
        from tkinter import ttk

        for child in self.detail_inner.winfo_children():
            child.destroy()
        if self._state.get("mem") is None:
            return
        visible = _visible_detail_rows(
            self._state.get("rows", []), changed_only=self.changed_only_var.get(),
            search=self.detail_search_var.get(),
        )
        self._state["visible"] = [r["name"] for r in visible]

        def _add(node, depth):
            if not node["children"]:
                self._add_detail_row(node["item"], label=node["label"], depth=depth)
                return
            n = len(_tree_items(node))
            ttk.Label(
                self.detail_inner, text=f"{node['label']} ({n})", font=("TkDefaultFont", 9, "bold"),
            ).pack(anchor="w", pady=(4, 0), padx=(16 * depth, 0))
            if node["item"] is not None:
                self._add_detail_row(node["item"], label=f"{node['label']} (own value)", depth=depth + 1)
            for c in node["children"]:
                _add(c, depth + 1)

        for c in _name_tree(visible)["children"]:
            _add(c, 0)
        self._update_detail_count()

    def _on_recall(self):
        from tkinter import messagebox

        if self._state.get("mem") is None:
            self._set_status("load a memory first", error=True)
            return
        selected_rows = self._selected_detail_rows()
        if not selected_rows:
            self._set_status("no shown rows selected to recall", error=True)
            return
        n_changed = sum(1 for r in selected_rows if r["changed"])
        if not messagebox.askyesno(
            "Confirm recall",
            f"Recall {len(selected_rows)} selected value(s)? {n_changed} will actually change.",
            parent=self.top,
        ):
            return
        filtered = {r["name"]: r["memory"] for r in selected_rows}
        try:
            self.memory.recall(input_obj={"settings": filtered}, force=True, selection="settings")
            recall_error = None
        except Exception as e:
            recall_error = e
        try:
            self._refresh_detail()
        except Exception:
            pass  # _diff_rows already guards each row's own read individually
        if recall_error is None:
            self._set_status("recall complete")
        else:
            self._set_status(_recall_error_status(recall_error), error=True)

    def _on_export(self):
        from tkinter import filedialog

        mem = self._state.get("mem")
        if mem is None:
            self._set_status("load a memory first", error=True)
            return
        default_name = _default_export_name(self.memory, _export_label(self._state))
        path = filedialog.asksaveasfilename(
            parent=self.top,
            title="Export memory to file",
            initialfile=default_name,
            defaultextension=".json",
            filetypes=_JSON_FILETYPES_TK,
        )
        if not path:
            return
        try:
            _export_to_file(mem, path)
            self._set_status(f"exported to {path}")
        except Exception as e:
            self._set_status(f"export failed: {e}", error=True)

    def _on_save(self):
        message = self.message_var.get().strip()
        if not message:
            self._set_status("enter a message before saving", error=True)
            return
        selection = self.save_selection_var.get().strip() or "settings"
        preset_name = self.save_preset_var.get().strip() or None
        try:
            self.memory.memorize(
                message=message, force_message=False, to_elog=self.elog_var.get(),
                selection=selection, preset_varname=preset_name,
            )
            self.message_var.set("")
            self.save_preset_var.set("")
            self._set_status("saved new memory")
            self._refresh_overview()
        except Exception as e:
            self._set_status(f"save failed: {e}", error=True)


def make_memory_browser_tk(assembly, parent=None):
    return MemoryBrowserTk(assembly, parent=parent)


# ---------------------------------------------------------------------------
# dispatcher, mirrors Assembly.widget()
# ---------------------------------------------------------------------------


def make_memory_browser(assembly, parent=None):
    """Build the memory browser in whichever backend `assembly.widget()`
    would use: ipywidgets in a notebook, else Qt, falling back to Tk if Qt
    isn't importable."""
    from eco.utilities.utilities import is_notebook

    if is_notebook():
        return make_memory_browser_ipywidgets(assembly)
    try:
        return make_memory_browser_qt(assembly, parent=parent)
    except ImportError:
        return make_memory_browser_tk(assembly, parent=parent)
