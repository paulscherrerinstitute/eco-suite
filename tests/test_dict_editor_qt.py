"""The Qt grid's dict/list editor (eco.widgets.dict_editor_qt): a dict or
list value gets "{…} Edit" (adjustable) or "{…} View" (detector) instead of
"n/a"; edits keep each leaf's type, read-only leaves stay unchanged, and
Save writes the whole value once through the row's background write."""

import time

import numpy as np
import pytest

pytest.importorskip("qtpy")

from qtpy import QtWidgets

from eco.elements.adjustable import AdjustableMemory
from eco.elements.detector import DetectorGet
from eco.widgets import display_qt
from eco.widgets.dict_editor_qt import KEY, VALUE, StructuredValueDialog


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(app, seconds, until):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


CONFIG = {
    "options": ["cube"],
    "cameras": {"inline": {"pvname": "SARES20-CAMS142-M2", "serial_number": 23067644}},
    "speed": 250.0,
    "enabled": True,
    "shape": (2, 3),
}


def find(dialog, *path):
    node = dialog.tree.invisibleRootItem()
    for key in path:
        node = next(node.child(i) for i in range(node.childCount())
                    if node.child(i).text(KEY) in (str(key), f"[{key}]"))
    return node


def test_editing_keeps_the_types(app):
    written = []
    dialog = StructuredValueDialog(None, write=written.append, value=CONFIG)
    find(dialog, "speed").setText(VALUE, "300")             # float stays float
    find(dialog, "cameras", "inline", "serial_number").setText(VALUE, "40298884")
    find(dialog, "enabled").setText(VALUE, "false")
    assert dialog.save()
    (value,) = written
    assert value["speed"] == 300.0 and isinstance(value["speed"], float)
    assert value["cameras"]["inline"]["serial_number"] == 40298884
    assert value["enabled"] is False
    assert value["shape"] == (2, 3) and isinstance(value["shape"], tuple)
    assert value["options"] == ["cube"]
    assert CONFIG["speed"] == 250.0  # the original is untouched


def test_a_wrong_leaf_is_not_saved(app):
    written = []
    dialog = StructuredValueDialog(None, write=written.append, value=CONFIG)
    find(dialog, "speed").setText(VALUE, "fast")
    assert not dialog.save() and written == []
    assert "not saved" in dialog.status.text() and "speed" in dialog.status.text()


def test_adding_and_removing_entries(app):
    written = []
    dialog = StructuredValueDialog(None, write=written.append, value={"cameras": {}, "options": ["cube"]})
    dialog.tree.setCurrentItem(find(dialog, "cameras"))
    new = dialog.add_entry("dict")
    new.setText(KEY, "sideview")
    dialog.tree.setCurrentItem(new)
    leaf = dialog.add_entry("leaf")
    leaf.setText(KEY, "serial_number")
    leaf.setText(VALUE, "22937745")
    dialog.tree.setCurrentItem(find(dialog, "options"))
    dialog.add_entry("leaf").setText(VALUE, "ottifant")
    dialog.tree.setCurrentItem(find(dialog, "options", 0))
    dialog.remove_selected()
    assert dialog.save()
    assert written == [{"cameras": {"sideview": {"serial_number": 22937745}}, "options": ["ottifant"]}]


def test_duplicate_keys_are_refused(app):
    dialog = StructuredValueDialog(None, write=lambda v: None, value={"a": 1, "b": 2})
    find(dialog, "b").setText(KEY, "a")
    assert not dialog.save()
    assert "appears twice" in dialog.status.text()


def test_a_value_that_is_not_plain_is_kept_and_not_editable(app):
    array = np.arange(3)
    written = []
    dialog = StructuredValueDialog(None, write=written.append, value={"data": array, "n": 1})
    node = find(dialog, "data")
    node.setText(VALUE, "[9, 9]")  # its value cannot be edited (the key can)
    assert node.text(VALUE) == repr(array)
    assert dialog.save()
    assert written[0]["data"] is array


def test_view_only_has_no_editing(app):
    dialog = StructuredValueDialog(None, write=None, value=CONFIG)
    assert not (int(find(dialog, "speed").flags()) & int(display_qt.QtCore.Qt.ItemIsEditable))
    assert not hasattr(dialog, "save_button")


def test_the_grid_offers_edit_and_view_and_reads_in_the_background(app):
    adjustable = AdjustableMemory(dict(CONFIG), name="thc_config")
    controls, _ = display_qt._build_item_controls_qt(adjustable, QtWidgets.QLabel(""), dict(CONFIG))
    button = next(b for b in controls.findChildren(QtWidgets.QPushButton) if b.text() == "{…} Edit")

    detector = DetectorGet(lambda: {"a": 1}, name="status")
    view_controls, _ = display_qt._build_item_controls_qt(detector, QtWidgets.QLabel(""), {"a": 1})
    assert any(b.text() == "{…} View" for b in view_controls.findChildren(QtWidgets.QPushButton))

    dialog = StructuredValueDialog(adjustable, write=lambda v: controls.controller.write(lambda: v))
    assert pump(app, 5, lambda: dialog.tree.topLevelItemCount() == len(CONFIG))
    find(dialog, "speed").setText(VALUE, "123.5")
    assert dialog.save()
    assert pump(app, 5, lambda: adjustable.get_current_value()["speed"] == 123.5)
    assert button.isEnabled()
