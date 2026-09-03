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


def _group_rows(rows):
    """Group `_diff_rows` output by top-level sub-component (same key
    `Assembly.get_display_str()`/`section_row_styles` use), preserving
    first-seen order -- the "unfold sub-components" grouping."""
    groups = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row)
    return groups


def _foldable_sections(rows):
    """Group `_diff_rows` output for rendering: a group with more than one
    row becomes a real foldable section; a group with exactly one row is
    reported as a bare row instead -- spending a whole extra fold/unfold
    header row on a single value is pure overhead for no benefit (same
    "singleton groups don't get special treatment" rule
    `eco.utilities.tables.section_row_styles` already applies to the
    terminal table, just never carried over here until now). Returns an
    ordered list of `("section", group_name, rows)` / `("row", None, row)`
    tuples, in first-seen order."""
    grouped = _group_rows(rows)
    out = []
    for name, group_rows in grouped.items():
        if len(group_rows) > 1:
            out.append(("section", name, group_rows))
        else:
            out.append(("row", None, group_rows[0]))
    return out


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


class MemoryBrowserQt:
    def __init__(self, assembly, parent=None):
        self.assembly = assembly
        self.memory = assembly.memory
        self.parent = parent
        self.window = None
        self._state = {
            "key": None, "path": None, "mem": None, "rows": [], "checked": {},
            "source_ancestor_name": None,
        }
        self._group_checks = {}
        self._build()

    def _build(self):
        from qtpy import QtWidgets, QtCore

        self.window = QtWidgets.QWidget(self.parent)
        self.window.setWindowTitle(
            f"Memories - {self.assembly.alias.get_full_name()}"
        )
        if self.parent is None:
            self.window.setAttribute(QtCore.Qt.WA_QuitOnClose, False)
        # so this.window really is destroyed (not just hidden) when closed
        # via its own native close (X) button -- otherwise the destroyed
        # hook below never fires there, and a caller's "already open?
        # just raise it" check (see AxisPTZStreamQt._open_memories) keeps
        # seeing a stale, non-None .window that's actually closed
        self.window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        outer = QtWidgets.QVBoxLayout(self.window)

        # --- overview list -------------------------------------------------
        outer.addWidget(QtWidgets.QLabel("<b>Stored memories</b>"))

        overview_filter_row = QtWidgets.QHBoxLayout()
        overview_filter_row.addWidget(QtWidgets.QLabel("Selection:"))
        self.overview_selection_combo = QtWidgets.QComboBox()
        self.overview_selection_combo.setEditable(True)
        self.overview_selection_combo.currentTextChanged.connect(self._refresh_overview)
        overview_filter_row.addWidget(self.overview_selection_combo)
        self.show_parents_cb = QtWidgets.QCheckBox("show parent memories too")
        self.show_parents_cb.stateChanged.connect(self._refresh_overview)
        overview_filter_row.addWidget(self.show_parents_cb)
        overview_filter_row.addStretch(1)
        outer.addLayout(overview_filter_row)

        self.list = QtWidgets.QListWidget()
        self.list.setMaximumHeight(160)
        self.list.itemClicked.connect(self._on_pick_stored)
        outer.addWidget(self.list)

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
        load_row.addStretch(1)
        outer.addLayout(load_row)

        # --- filters ---------------------------------------------------
        self.filter_box = QtWidgets.QGroupBox("Filters (within an opened memory)")
        filter_layout = QtWidgets.QVBoxLayout(self.filter_box)
        self.group_row = QtWidgets.QHBoxLayout()
        self.group_row.addWidget(QtWidgets.QLabel("Keyword groups:"))
        filter_layout.addLayout(self.group_row)
        self.changed_only_cb = QtWidgets.QCheckBox("show changed only")
        self.changed_only_cb.setChecked(True)
        self.changed_only_cb.stateChanged.connect(self._refresh_detail)
        filter_layout.addWidget(self.changed_only_cb)
        outer.addWidget(self.filter_box)

        # --- detail tree -----------------------------------------------
        outer.addWidget(QtWidgets.QLabel("<b>Details</b> (checkbox selects for recall)"))
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["name", "present", "memory", "diff", "status"])
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        outer.addWidget(self.tree)

        select_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("select all")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn = QtWidgets.QPushButton("select none")
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        select_row.addWidget(select_all_btn)
        select_row.addWidget(select_none_btn)
        select_row.addStretch(1)
        outer.addLayout(select_row)

        action_row = QtWidgets.QHBoxLayout()
        recall_btn = QtWidgets.QPushButton("Recall selected values")
        recall_btn.clicked.connect(self._on_recall)
        export_btn = QtWidgets.QPushButton("Export to file…")
        export_btn.clicked.connect(self._on_export)
        action_row.addWidget(recall_btn)
        action_row.addWidget(export_btn)
        action_row.addStretch(1)
        outer.addLayout(action_row)

        # --- save new memory ---------------------------------------------
        save_box = QtWidgets.QGroupBox("Save current state as a new memory")
        save_vbox = QtWidgets.QVBoxLayout(save_box)
        save_layout = QtWidgets.QHBoxLayout()
        self.message_edit = QtWidgets.QLineEdit()
        self.message_edit.setPlaceholderText("message for this memory…")
        save_layout.addWidget(self.message_edit)
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
        save_vbox.addLayout(save_layout)

        # opt-in, non-default item picker: unchecked, this row/list stays
        # hidden and Save captures everything the selection resolves to,
        # exactly as before this existed.
        pick_row = QtWidgets.QHBoxLayout()
        self.pick_items_cb = QtWidgets.QCheckBox("choose items…")
        self.pick_items_cb.setToolTip(
            "narrow this save down to hand-picked items instead of "
            "capturing everything the selection above resolves to"
        )
        self.pick_items_cb.stateChanged.connect(self._on_toggle_pick_items)
        pick_row.addWidget(self.pick_items_cb)
        refresh_items_btn = QtWidgets.QPushButton("Refresh items")
        refresh_items_btn.clicked.connect(self._refresh_pick_items)
        pick_row.addWidget(refresh_items_btn)
        pick_all_btn = QtWidgets.QPushButton("all")
        pick_all_btn.clicked.connect(lambda: self._set_all_pick_items(True))
        pick_row.addWidget(pick_all_btn)
        pick_none_btn = QtWidgets.QPushButton("none")
        pick_none_btn.clicked.connect(lambda: self._set_all_pick_items(False))
        pick_row.addWidget(pick_none_btn)
        pick_row.addStretch(1)
        save_vbox.addLayout(pick_row)

        self.pick_items_list = QtWidgets.QListWidget()
        self.pick_items_list.setMaximumHeight(140)
        self.pick_items_list.setVisible(False)
        save_vbox.addWidget(self.pick_items_list)
        outer.addWidget(save_box)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.window.close)
        outer.addWidget(close_btn)

        self.status_label = QtWidgets.QLabel("")
        outer.addWidget(self.status_label)

        self._refresh_overview()
        self.window.resize(780, 760)
        # so a stale, already-deleted window can't be mistaken for a still-
        # open one (e.g. by a caller's "already open, just raise it" check
        # -- see eco.widgets.camera_stream_qt.AxisPTZStreamQt._open_memories)
        # when the user closes this via the window's own native close (X)
        # button rather than through this class's own Close button/API
        self.window.destroyed.connect(lambda *a: setattr(self, "window", None))
        self.window.show()

    def _set_status(self, text, error=False):
        color = "#c62828" if error else "#2e7d32"
        self.status_label.setText(f"<span style='color:{color}'>{text}</span>")

    def _refresh_overview(self, *_a):
        # *_a swallows whatever positional arg the triggering Qt signal
        # passes (a bool from a button, a str from the selection combo) --
        # neither is used, both re-read live state.
        from qtpy import QtWidgets, QtGui

        own_rows = _overview_rows(self.memory)
        all_rows = list(own_rows)
        if self.show_parents_cb.isChecked():
            all_rows += _ancestor_overview_rows(self.memory)
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

        self._overview = _filter_overview_rows(all_rows, current)
        ancestor_names = [r["ancestor_name"] for r in self._overview if "ancestor_name" in r]
        color_map = _ancestor_color_map(ancestor_names)
        self.list.clear()
        for row in self._overview:
            date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "?"
            groups = ",".join(row["groups"])
            label = f"{date_str}   {row['message']}   [{groups}]"
            ancestor_name = row.get("ancestor_name")
            if ancestor_name:
                label = f"[{ancestor_name}] {label}"
            item = QtWidgets.QListWidgetItem(label)
            if ancestor_name:
                item.setBackground(QtGui.QColor(color_map[ancestor_name]))
            self.list.addItem(item)

        if color_map:
            self.legend_label.setText(
                "Parent memory colours: " + "; ".join(
                    f'<span style="background-color:{c};">&nbsp;{n}&nbsp;</span>'
                    for n, c in color_map.items()
                )
            )
        else:
            self.legend_label.setText("")

    def _on_pick_stored(self, item):
        idx = self.list.row(item)
        row = self._overview[idx]
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
            else:
                mem = self.memory.get_memory(key=row["key"])
                self._load_mem(mem, key=row["key"], path=None)
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
        self._load_mem(mem, key=None, path=path)

    def _load_mem(self, mem, key, path, source_ancestor_name=None):
        self._state = {
            "key": key, "path": path, "mem": mem, "rows": [], "checked": {},
            "source_ancestor_name": source_ancestor_name,
        }
        groups = _available_recall_groups(self.memory, mem)
        # rebuild the group-filter checkboxes for this memory's own groups
        while self.group_row.count() > 1:
            child = self.group_row.takeAt(1)
            if child.widget():
                child.widget().deleteLater()
        self._group_checks = {}
        from qtpy import QtWidgets

        for g in groups:
            cb = QtWidgets.QCheckBox(g)
            cb.setChecked(True)
            cb.stateChanged.connect(self._refresh_detail)
            self.group_row.addWidget(cb)
            self._group_checks[g] = cb
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
        for r in self._state.get("rows", []):
            self._state["checked"][r["name"]] = value
        self._refresh_detail()

    def _on_tree_item_changed(self, item, column):
        if getattr(self, "_suppress_item_changed", False) or column != 0:
            return
        from qtpy import QtCore

        name = item.data(0, QtCore.Qt.UserRole)
        if name is None:
            return
        self._state["checked"][name] = item.checkState(0) == QtCore.Qt.Checked

    def _add_leaf_item(self, container, r, top_level=False):
        from qtpy import QtWidgets, QtCore

        status = "not found" if not r["available"] else ("CHANGED" if r["changed"] else "==")
        present = _format_value(r["present"]) if r["available"] else "?"
        item = QtWidgets.QTreeWidgetItem(
            [r["name"], present, _format_value(r["memory"]), r["diff"], status]
        )
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(
            0, QtCore.Qt.Checked if self._state["checked"].get(r["name"], True) else QtCore.Qt.Unchecked
        )
        item.setData(0, QtCore.Qt.UserRole, r["name"])
        if r["changed"]:
            for col in range(5):
                item.setForeground(col, QtWidgets.QApplication.palette().link())
        if top_level:
            self.tree.addTopLevelItem(item)
        else:
            container.addChild(item)

    def _refresh_detail(self, *_a):
        mem = self._state.get("mem")
        self.tree.clear()
        if mem is None:
            return
        rows = _diff_rows(self.memory, mem, selection=self._selected_selection())
        self._state["rows"] = rows
        for r in rows:
            self._state["checked"].setdefault(r["name"], True)
        show_changed_only = self.changed_only_cb.isChecked()
        from qtpy import QtWidgets

        self._suppress_item_changed = True
        for kind, group_name, payload in _foldable_sections(rows):
            if kind == "section":
                visible_rows = [r for r in payload if (r["changed"] or not show_changed_only)]
                if not visible_rows:
                    continue
                parent_item = QtWidgets.QTreeWidgetItem([group_name, "", "", "", ""])
                self.tree.addTopLevelItem(parent_item)
                for r in visible_rows:
                    self._add_leaf_item(parent_item, r)
                parent_item.setExpanded(True)
            else:
                r = payload
                if r["changed"] or not show_changed_only:
                    self._add_leaf_item(self.tree, r, top_level=True)
        self._suppress_item_changed = False
        for col in range(5):
            self.tree.resizeColumnToContents(col)

    def _on_recall(self):
        from qtpy import QtWidgets

        if self._state.get("mem") is None:
            self._set_status("load a memory first", error=True)
            return
        checked = self._state["checked"]
        selected_rows = [r for r in self._state["rows"] if checked.get(r["name"], True)]
        if not selected_rows:
            self._set_status("no rows selected to recall", error=True)
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

    def _on_toggle_pick_items(self, _state):
        self.pick_items_list.setVisible(self.pick_items_cb.isChecked())
        if self.pick_items_cb.isChecked():
            self._refresh_pick_items()
        else:
            self.pick_items_list.clear()

    def _refresh_pick_items(self):
        from qtpy import QtWidgets, QtCore

        if not self.pick_items_cb.isChecked():
            return
        selection = self.save_selection_combo.currentText().strip() or "settings"
        try:
            pairs = self.memory.get_memorize_candidates(selection=selection)
        except Exception as e:
            self._set_status(f"could not list items: {e}", error=True)
            return
        multi_group = len({p[0] for p in pairs}) > 1
        self.pick_items_list.clear()
        for group, name, value in pairs:
            text = f"[{group}] {name}  =  {value}" if multi_group else f"{name}  =  {value}"
            item = QtWidgets.QListWidgetItem(text)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked)
            item.setData(QtCore.Qt.UserRole, name)
            self.pick_items_list.addItem(item)

    def _set_all_pick_items(self, value):
        from qtpy import QtCore

        state = QtCore.Qt.Checked if value else QtCore.Qt.Unchecked
        for i in range(self.pick_items_list.count()):
            self.pick_items_list.item(i).setCheckState(state)

    def _on_save(self):
        from qtpy import QtCore

        message = self.message_edit.text().strip()
        if not message:
            self._set_status("enter a message before saving", error=True)
            return
        selection = self.save_selection_combo.currentText().strip() or "settings"
        kwargs = {}
        if self.pick_items_cb.isChecked():
            names = [
                self.pick_items_list.item(i).data(QtCore.Qt.UserRole)
                for i in range(self.pick_items_list.count())
                if self.pick_items_list.item(i).checkState() == QtCore.Qt.Checked
            ]
            if not names:
                self._set_status("no items selected -- uncheck 'choose items' to save everything, or check at least one", error=True)
                return
            kwargs["pick_items"] = names
        try:
            self.memory.memorize(
                message=message, force_message=False, to_elog=self.elog_cb.isChecked(),
                selection=selection, **kwargs,
            )
            self.message_edit.clear()
            self._set_status("saved new memory")
            self._refresh_overview()
        except Exception as e:
            self._set_status(f"save failed: {e}\n{traceback.format_exc(limit=2)}", error=True)


def make_memory_browser_qt(assembly, parent=None):
    return MemoryBrowserQt(assembly, parent=parent)


# ---------------------------------------------------------------------------
# ipywidgets backend
# ---------------------------------------------------------------------------


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
    overview_style_html = widgets.HTML("")  # injected <style> for ancestor row colours
    overview_box = widgets.VBox([], layout=widgets.Layout(
        max_height="180px", overflow_y="auto", border="1px solid #ccc",
    ))
    legend_html = widgets.HTML("")

    refresh_btn = widgets.Button(description="Refresh list")
    load_path_text = widgets.Text(
        placeholder="path to a memory .json file to load",
        layout=widgets.Layout(width="70%"),
    )
    load_file_btn = widgets.Button(description="Load from file")

    group_filter_box = widgets.HBox([widgets.Label("Keyword groups:")])
    changed_only_cb = widgets.Checkbox(value=True, description="show changed only")

    detail_box = widgets.VBox([])
    select_all_btn = widgets.Button(description="select all")
    select_none_btn = widgets.Button(description="select none")
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
    items_pick_box = widgets.VBox([])
    pick_item_checks = {}

    group_checks = {}

    def _set_status(text, error=False):
        color = "#c62828" if error else "#2e7d32"
        status_html.value = f"<span style='color:{color}'>{text}</span>"

    def _load_row(row):
        ancestor = row.get("ancestor")
        try:
            if ancestor is not None:
                entry = ancestor.memory.get_memory(key=row["key"])
                sliced = memory.get_ancestor_recall_dict(ancestor, entry, selection=None)
                _load_mem(
                    {"settings": sliced}, key=None, path=None,
                    source_ancestor_name=row["ancestor_name"],
                )
            else:
                mem = memory.get_memory(key=row["key"])
                _load_mem(mem, key=row["key"], path=None)
        except Exception as e:
            _set_status(f"could not load memory: {e}", error=True)

    def _refresh_overview(_b=None):
        own_rows = _overview_rows(memory)
        all_rows = list(own_rows)
        if show_parents_cb.value:
            all_rows += _ancestor_overview_rows(memory)
        overview_selection_combo.options = ["all"] + _known_selection_tags(all_rows)
        tag = overview_selection_combo.value or "settings"
        rows = _filter_overview_rows(all_rows, tag)

        ancestor_names = [r["ancestor_name"] for r in rows if "ancestor_name" in r]
        color_map = _ancestor_color_map(ancestor_names)
        style_rules = []
        row_widgets = []
        for row in rows:
            date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "?"
            groups = ",".join(row["groups"])
            ancestor_name = row.get("ancestor_name")
            label = f"{date_str}   {row['message']}   [{groups}]"
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
        overview_style_html.value = "<style>" + "".join(style_rules) + "</style>"
        if color_map:
            legend_html.value = "Parent memory colours: " + "; ".join(
                f'<span style="background-color:{c};">&nbsp;{n}&nbsp;</span>'
                for n, c in color_map.items()
            )
        else:
            legend_html.value = ""

    def _make_row_widget(r):
        cb = widgets.Checkbox(
            value=state["checked"].get(r["name"], True), indent=False,
            layout=widgets.Layout(width="30px"),
        )

        def _on_cb_change(change, name=r["name"]):
            if change.get("name") == "value":
                state["checked"][name] = change["new"]

        cb.observe(_on_cb_change, names="value")
        if not r["available"]:
            status = "not found"
        elif r["changed"]:
            status = "<b style='color:#c62828'>CHANGED</b>"
        else:
            status = "=="
        present = _format_value(r["present"]) if r["available"] else "?"
        label = widgets.HTML(
            f"<span>{r['name']}</span> &nbsp; present: <b>{present}</b> &nbsp; "
            f"memory: <b>{_format_value(r['memory'])}</b> &nbsp; diff: {r['diff']} "
            f"&nbsp; {status}"
        )
        return widgets.HBox([cb, label])

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
        show_changed_only = changed_only_cb.value

        sections = []
        for kind, group_name, payload in _foldable_sections(rows):
            if kind == "section":
                visible = [r for r in payload if (r["changed"] or not show_changed_only)]
                if not visible:
                    continue
                body = widgets.VBox([_make_row_widget(r) for r in visible])
                acc = widgets.Accordion(children=[body])
                acc.set_title(0, f"{group_name} ({len(visible)})")
                acc.selected_index = 0
                sections.append(acc)
            else:
                r = payload
                if r["changed"] or not show_changed_only:
                    sections.append(_make_row_widget(r))
        detail_box.children = tuple(sections) if sections else (widgets.HTML("(no rows)"),)

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

    def _select_all(_b):
        for r in state.get("rows", []):
            state["checked"][r["name"]] = True
        _refresh_detail()

    def _select_none(_b):
        for r in state.get("rows", []):
            state["checked"][r["name"]] = False
        _refresh_detail()

    def _on_recall(_b):
        if state.get("mem") is None:
            _set_status("load a memory first", error=True)
            return
        checked = state["checked"]
        selected_rows = [r for r in state["rows"] if checked.get(r["name"], True)]
        if not selected_rows:
            _set_status("no rows selected to recall", error=True)
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

    def _make_pick_item_row(group, name, value, multi_group):
        cb = widgets.Checkbox(value=True, indent=False, layout=widgets.Layout(width="30px"))
        pick_item_checks[name] = cb
        prefix = f"[{group}] " if multi_group else ""
        label = widgets.HTML(f"<span>{prefix}{name}</span> &nbsp; = &nbsp; {_format_value(value)}")
        return widgets.HBox([cb, label])

    def _refresh_pick_items(_b=None):
        if not pick_items_cb.value:
            return
        selection = save_selection_combo.value.strip() or "settings"
        try:
            pairs = memory.get_memorize_candidates(selection=selection)
        except Exception as e:
            _set_status(f"could not list items: {e}", error=True)
            return
        multi_group = len({p[0] for p in pairs}) > 1
        pick_item_checks.clear()
        items_pick_box.children = tuple(
            _make_pick_item_row(g, n, v, multi_group) for g, n, v in pairs
        ) or (widgets.Label("(no items)"),)

    def _on_toggle_pick_items(change):
        if change.get("name") != "value":
            return
        if change["new"]:
            _refresh_pick_items()
        else:
            pick_item_checks.clear()
            items_pick_box.children = ()

    def _set_all_pick_items(value):
        for cb in pick_item_checks.values():
            cb.value = value

    def _on_save(_b):
        message = message_text.value.strip()
        if not message:
            _set_status("enter a message before saving", error=True)
            return
        selection = save_selection_combo.value.strip() or "settings"
        kwargs = {}
        if pick_items_cb.value:
            names = [name for name, cb in pick_item_checks.items() if cb.value]
            if not names:
                _set_status(
                    "no items selected -- uncheck 'choose items' to save "
                    "everything, or check at least one", error=True,
                )
                return
            kwargs["pick_items"] = names
        try:
            memory.memorize(
                message=message, force_message=False, to_elog=elog_cb.value,
                selection=selection, **kwargs,
            )
            message_text.value = ""
            _set_status("saved new memory")
            _refresh_overview()
        except Exception as e:
            _set_status(f"save failed: {e}", error=True)

    overview_selection_combo.observe(_refresh_overview, names="value")
    show_parents_cb.observe(_refresh_overview, names="value")
    refresh_btn.on_click(_refresh_overview)
    load_file_btn.on_click(_on_load_file)
    changed_only_cb.observe(_refresh_detail, names="value")
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

    box = widgets.VBox(
        [
            title,
            widgets.HTML("<b>Stored memories</b>"),
            widgets.HBox([overview_selection_combo, show_parents_cb]),
            overview_style_html,
            overview_box,
            legend_html,
            widgets.HBox([refresh_btn, load_path_text, load_file_btn]),
            group_filter_box,
            changed_only_cb,
            widgets.HTML("<b>Details</b> (checkbox selects for recall)"),
            detail_box,
            widgets.HBox([select_all_btn, select_none_btn]),
            widgets.HBox([recall_btn, export_path_text, export_btn]),
            widgets.HTML("<b>Save current state as a new memory</b>"),
            widgets.HBox([message_text, save_selection_combo, elog_cb, save_btn]),
            widgets.HBox([
                pick_items_cb, refresh_items_btn, pick_all_items_btn, pick_none_items_btn,
            ]),
            items_pick_box,
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
            "<<ComboboxSelected>>", lambda _evt: self._refresh_overview()
        )
        self.overview_selection_combo.bind(
            "<Return>", lambda _evt: self._refresh_overview()
        )
        self.show_parents_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            overview_filter_row, text="show parent memories too",
            variable=self.show_parents_var, command=self._refresh_overview,
        ).pack(side="left", padx=(8, 0))

        list_frame = ttk.Frame(self.top)
        list_frame.pack(fill="x", padx=4)
        self.overview_list = tk.Listbox(list_frame, height=7)
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

        filter_box = ttk.LabelFrame(self.top, text="Filters (within an opened memory)")
        filter_box.pack(fill="x", padx=4, pady=4)
        self.group_row = ttk.Frame(filter_box)
        self.group_row.pack(fill="x", padx=4, pady=2)
        ttk.Label(self.group_row, text="Keyword groups:").pack(side="left")
        self.changed_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filter_box, text="show changed only", variable=self.changed_only_var,
            command=self._refresh_detail,
        ).pack(anchor="w", padx=4)

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
        ttk.Button(select_row, text="select all", command=lambda: self._set_all_checked(True)).pack(
            side="left"
        )
        ttk.Button(
            select_row, text="select none", command=lambda: self._set_all_checked(False)
        ).pack(side="left", padx=(4, 0))

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
        self.overview_selection_combo["values"] = ["all"] + _known_selection_tags(all_rows)
        tag = self.overview_selection_var.get() or "settings"
        self._overview = _filter_overview_rows(all_rows, tag)
        ancestor_names = [r["ancestor_name"] for r in self._overview if "ancestor_name" in r]
        color_map = _ancestor_color_map(ancestor_names)
        self.overview_list.delete(0, "end")
        for i, row in enumerate(self._overview):
            date_str = row["date"].strftime("%Y-%m-%d %H:%M") if row["date"] else "?"
            groups = ",".join(row["groups"])
            ancestor_name = row.get("ancestor_name")
            label = f"{date_str}   {row['message']}   [{groups}]"
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
            else:
                mem = self.memory.get_memory(key=row["key"])
                self._load_mem(mem, key=row["key"], path=None)
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
        for r in self._state.get("rows", []):
            self._state["checked"][r["name"]] = value
        self._refresh_detail()

    def _add_detail_row(self, r):
        import tkinter as tk
        from tkinter import ttk

        row_frame = ttk.Frame(self.detail_inner)
        row_frame.pack(fill="x", anchor="w")
        var = tk.BooleanVar(value=self._state["checked"].get(r["name"], True))

        def _on_toggle(name=r["name"], v=var):
            self._state["checked"][name] = v.get()

        ttk.Checkbutton(row_frame, variable=var, command=_on_toggle).pack(side="left")
        status = "not found" if not r["available"] else ("CHANGED" if r["changed"] else "==")
        present = _format_value(r["present"]) if r["available"] else "?"
        text = (
            f"{r['name']}   present: {present}   memory: {_format_value(r['memory'])}   "
            f"diff: {r['diff']}   {status}"
        )
        lbl = ttk.Label(row_frame, text=text)
        if r["changed"]:
            lbl.configure(foreground="#1565c0")
        lbl.pack(side="left")

    def _refresh_detail(self):
        from tkinter import ttk

        for child in self.detail_inner.winfo_children():
            child.destroy()
        mem = self._state.get("mem")
        if mem is None:
            return
        rows = _diff_rows(self.memory, mem, selection=self._selected_selection())
        self._state["rows"] = rows
        for r in rows:
            self._state["checked"].setdefault(r["name"], True)
        show_changed_only = self.changed_only_var.get()

        for kind, group_name, payload in _foldable_sections(rows):
            if kind == "section":
                visible = [r for r in payload if (r["changed"] or not show_changed_only)]
                if not visible:
                    continue
                ttk.Label(
                    self.detail_inner, text=group_name, font=("TkDefaultFont", 9, "bold"),
                ).pack(anchor="w", pady=(6, 0))
                for r in visible:
                    self._add_detail_row(r)
            else:
                r = payload
                if r["changed"] or not show_changed_only:
                    self._add_detail_row(r)

    def _on_recall(self):
        from tkinter import messagebox

        if self._state.get("mem") is None:
            self._set_status("load a memory first", error=True)
            return
        checked = self._state["checked"]
        selected_rows = [r for r in self._state["rows"] if checked.get(r["name"], True)]
        if not selected_rows:
            self._set_status("no rows selected to recall", error=True)
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
        try:
            self.memory.memorize(
                message=message, force_message=False, to_elog=self.elog_var.get(),
                selection=selection,
            )
            self.message_var.set("")
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
