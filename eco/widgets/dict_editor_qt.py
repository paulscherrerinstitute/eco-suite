"""A tree editor for dict/list values, opened from the Qt property grid's
"{…} Edit" button (adjustables) or "{…} View" (detectors) in place of the
"n/a" such values used to get -- e.g. ``config_bernina.thc_config``.

Leaves keep their type: a float stays a float, a bool takes true/false; a
leaf that is not a plain value (a numpy array, an object) is shown by its
repr and left unchanged. Entries can be added (a typed value is read as a
Python literal: ``1``, ``2.5``, ``True``, ``"text"`` or plain text) and
removed at any level, and keys renamed. Nothing is written until Save, and
then the whole value at once, through the caller's `write` (the grid's
background write, so the window never blocks). The value is read in the
background when the dialog opens. See eco.widgets.structured_values for the
type handling shared with the web app.
"""

import threading

from qtpy import QtCore, QtGui, QtWidgets

from eco.widgets import structured_values as sv

ROLE = QtCore.Qt.UserRole + 1
KEY, VALUE, TYPE = 0, 1, 2
CONTAINERS = ("dict", "list", "tuple")


class _Reader(QtCore.QObject):
    done = QtCore.Signal(bool, object)


class StructuredValueDialog(QtWidgets.QDialog):
    """`item`: the adjustable/detector; `write(value)`: how to store the
    edited value (None: view only); `value`: show this instead of reading
    the item (tests, or a value already at hand)."""

    def __init__(self, item, write=None, title=None, value=None, parent=None, read=True):
        super().__init__(parent)
        self._item = item
        self._write = write
        self._original = None
        self.setWindowTitle(title or ("Edit " if write else "View ") + _name_of(item))
        self.resize(560, 480)

        layout = QtWidgets.QVBoxLayout(self)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["key", "value", "type"])
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(KEY, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(VALUE, QtWidgets.QHeaderView.Stretch)
        self.tree.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.SelectedClicked
        )
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree)

        self.status = QtWidgets.QLabel("reading…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QHBoxLayout()
        self._edit_buttons = []
        if write is not None:
            for label, kind in (("+ Entry", "leaf"), ("+ Dict", "dict"), ("+ List", "list")):
                button = QtWidgets.QPushButton(label)
                button.clicked.connect(lambda checked=False, k=kind: self.add_entry(k))
                buttons.addWidget(button)
                self._edit_buttons.append(button)
            remove = QtWidgets.QPushButton("Remove")
            remove.clicked.connect(lambda checked=False: self.remove_selected())
            buttons.addWidget(remove)
            self._edit_buttons.append(remove)
        buttons.addStretch(1)
        if write is not None:
            self.save_button = QtWidgets.QPushButton("Save")
            self.save_button.setDefault(True)
            self.save_button.clicked.connect(lambda checked=False: self.save())
            cancel = QtWidgets.QPushButton("Cancel")
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
            buttons.addWidget(self.save_button)
            self._edit_buttons.append(self.save_button)
        else:
            close = QtWidgets.QPushButton("Close")
            close.clicked.connect(self.reject)
            buttons.addWidget(close)
        layout.addLayout(buttons)
        for button in self._edit_buttons:
            button.setEnabled(False)

        if value is not None or not read:
            self.set_value(value)
        else:
            self._read_in_background()

    # -- reading -----------------------------------------------------------------------

    def _read_in_background(self):
        reader = _Reader(self)
        reader.done.connect(self._on_read)
        item = self._item

        def run():
            try:
                result = (True, item.get_current_value())
            except Exception as exc:
                result = (False, exc)
            try:
                reader.done.emit(*result)
            except RuntimeError:
                pass  # the dialog was closed meanwhile

        threading.Thread(target=run, daemon=True).start()

    def _on_read(self, ok, payload):
        try:
            if ok:
                self.set_value(payload)
            else:
                self.status.setText(f"could not read: {payload}")
        except RuntimeError:
            pass  # deleted meanwhile

    def set_value(self, value):
        self._original = value
        self.tree.blockSignals(True)
        self.tree.clear()
        root = self.tree.invisibleRootItem()
        if sv.is_structured(value):
            self._fill(root, value)
        else:
            self._add_node(root, "value", value)
        self.tree.expandToDepth(1)
        self.tree.blockSignals(False)
        for button in self._edit_buttons:
            button.setEnabled(True)
        self.status.setText(
            "Double-click a value or key to change it."
            if self._write is not None
            else "Read-only."
        )

    def _fill(self, parent_item, value):
        if isinstance(value, dict):
            for key, sub in value.items():
                self._add_node(parent_item, key, sub)
        else:
            for index, sub in enumerate(value):
                self._add_node(parent_item, index, sub)

    def _add_node(self, parent_item, key, value, fresh=False):
        node = QtWidgets.QTreeWidgetItem()
        parent_item.addChild(node)
        kind = sv.type_name(value)
        info = {"type": kind, "orig": value, "fresh": fresh, "key": key}
        node.setText(TYPE, kind)
        node.setData(KEY, ROLE, info)  # before the children: they ask for its kind
        if kind in CONTAINERS:
            node.setText(VALUE, _container_summary(value))
            self._fill(node, value)
        else:
            plain = sv.is_plain_leaf(value)
            info["readonly"] = not plain
            text = _leaf_text(value) if plain else repr(value)
            info["text"] = text
            node.setText(VALUE, text)
            if not plain:
                node.setForeground(VALUE, QtGui.QBrush(QtGui.QColor("gray")))
                node.setToolTip(VALUE, "not a plain value: shown, kept unchanged")
        node.setData(KEY, ROLE, info)
        self._set_key(node, key)
        self._set_flags(node)
        return node

    def _set_key(self, node, key):
        parent = node.parent() or self.tree.invisibleRootItem()
        parent_kind = _kind_of(parent) if node.parent() else (
            "dict" if isinstance(self._original, dict) or not sv.is_structured(self._original) else "list"
        )
        node.setText(KEY, str(key) if parent_kind == "dict" else f"[{key}]")

    def _set_flags(self, node):
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        info = node.data(KEY, ROLE)
        editable_leaf = info["type"] not in CONTAINERS and not info.get("readonly")
        if self._write is not None and (editable_leaf or self._in_dict(node)):
            flags |= QtCore.Qt.ItemIsEditable
        node.setFlags(flags)

    def _in_dict(self, node):
        if node.parent() is None:
            return isinstance(self._original, dict)
        return _kind_of(node.parent()) == "dict"

    # -- editing -----------------------------------------------------------------------

    def _on_item_changed(self, node, column):
        info = node.data(KEY, ROLE)
        if info is None:
            return
        self.tree.blockSignals(True)
        try:
            if column == VALUE and (info["type"] in CONTAINERS or info.get("readonly")):
                # only leaves are edited in place
                node.setText(VALUE, _container_summary(info["orig"]) if info["type"] in CONTAINERS else info["text"])
            elif column == KEY and not self._in_dict(node):
                self._set_key(node, node.parent().indexOfChild(node) if node.parent() else 0)
            elif column == VALUE:
                self._check_leaf(node)
        finally:
            self.tree.blockSignals(False)

    def _check_leaf(self, node):
        info = node.data(KEY, ROLE)
        try:
            sv.parse_leaf(node.text(VALUE), kind=None if info["fresh"] else info["type"])
            node.setBackground(VALUE, QtGui.QBrush())
            node.setToolTip(VALUE, "")
            return True
        except ValueError as exc:
            node.setBackground(VALUE, QtGui.QBrush(QtGui.QColor("#f4b6b6")))
            node.setToolTip(VALUE, str(exc))
            return False

    def _target_container(self):
        """The container a new entry goes into: the selected one, the
        parent of a selected leaf, else the top level."""
        node = self.tree.currentItem()
        if node is None:
            return self.tree.invisibleRootItem()
        if _kind_of(node) in CONTAINERS:
            return node
        return node.parent() or self.tree.invisibleRootItem()

    def add_entry(self, kind="leaf"):
        """A new entry in the container `_target_container` names: an
        empty-string leaf, or an empty dict or list."""
        parent = self._target_container()
        is_dict = (_kind_of(parent) == "dict") if parent is not self.tree.invisibleRootItem() else isinstance(self._original, dict)
        value = {"leaf": "", "dict": {}, "list": []}[kind]
        if is_dict:
            existing = {parent.child(i).text(KEY) for i in range(parent.childCount())}
            key = "new"
            n = 1
            while key in existing:
                n += 1
                key = f"new{n}"
        else:
            key = parent.childCount()
        self.tree.blockSignals(True)
        node = self._add_node(parent, key, value, fresh=True)
        if kind == "leaf":
            info = node.data(KEY, ROLE)
            info["type"] = "new"
            node.setData(KEY, ROLE, info)
            node.setText(TYPE, "new")
            node.setToolTip(VALUE, "a Python literal (1, 2.5, True, None, \"text\") or plain text")
        self._refresh_summaries(parent)
        self.tree.blockSignals(False)
        if parent is not self.tree.invisibleRootItem():
            parent.setExpanded(True)
        self.tree.setCurrentItem(node)
        self.tree.editItem(node, KEY if is_dict else VALUE)
        return node

    def remove_selected(self):
        node = self.tree.currentItem()
        if node is None:
            return
        parent = node.parent() or self.tree.invisibleRootItem()
        parent.removeChild(node)
        self.tree.blockSignals(True)
        if _kind_of(parent) != "dict" and not (parent is self.tree.invisibleRootItem() and isinstance(self._original, dict)):
            for i in range(parent.childCount()):
                self._set_key(parent.child(i), i)
        self._refresh_summaries(parent)
        self.tree.blockSignals(False)

    def _refresh_summaries(self, node):
        while node is not None and node is not self.tree.invisibleRootItem():
            info = node.data(KEY, ROLE)
            if info and info["type"] in CONTAINERS:
                n = node.childCount()
                node.setText(VALUE, "{%d}" % n if info["type"] == "dict" else "[%d]" % n)
            node = node.parent()

    # -- the value -----------------------------------------------------------------------

    def value(self):
        """The edited value; raises ValueError (and selects the entry) for a
        leaf that does not parse as its type, or a duplicate key."""
        root = self.tree.invisibleRootItem()
        if not sv.is_structured(self._original):
            if root.childCount() == 1:
                return self._value_of(root.child(0))
            raise ValueError("expected one value")
        if isinstance(self._original, dict):
            data = self._collect_dict(root)
        else:
            data = [self._value_of(root.child(i)) for i in range(root.childCount())]
            if isinstance(self._original, tuple):
                data = tuple(data)
        return sv.coerce_like(data, self._original)

    def _collect_dict(self, parent):
        out = {}
        for i in range(parent.childCount()):
            child = parent.child(i)
            key = child.text(KEY)
            if key in out:
                self.tree.setCurrentItem(child)
                raise ValueError(f"the key {key!r} appears twice")
            out[key] = self._value_of(child)
        return out

    def _value_of(self, node):
        info = node.data(KEY, ROLE)
        kind = info["type"]
        if kind == "dict":
            return self._collect_dict(node)
        if kind in ("list", "tuple"):
            items = [self._value_of(node.child(i)) for i in range(node.childCount())]
            return tuple(items) if kind == "tuple" else items
        if info.get("readonly"):
            return info["orig"]
        text = node.text(VALUE)
        if not info["fresh"] and text == info["text"]:
            return info["orig"]
        try:
            return sv.parse_leaf(text, kind=None if kind == "new" else kind)
        except ValueError as exc:
            self.tree.setCurrentItem(node)
            self._check_leaf(node)
            raise ValueError(f"{node.text(KEY)}: {exc}") from None

    def save(self):
        try:
            value = self.value()
        except ValueError as exc:
            self.status.setText(f"not saved: {exc}")
            return False
        self._write(value)
        self.accept()
        return True


def _kind_of(node):
    info = node.data(KEY, ROLE) if node is not None else None
    return info["type"] if info else None


def _container_summary(value):
    return ("{%d}" if isinstance(value, dict) else "[%d]") % len(value)


def _leaf_text(value):
    if value is None:
        return "None"
    plain = sv._plain_scalar(value)
    return str(plain)


def _name_of(item):
    try:
        return item.alias.get_full_name()
    except Exception:
        return getattr(item, "name", None) or "value"


def open_value_dialog(item, write=None, parent=None):
    """Open the editor (`write` given) or viewer for `item`'s value,
    non-modal; returns the dialog."""
    dialog = StructuredValueDialog(item, write=write, parent=parent)
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose)
    dialog.show()
    return dialog
