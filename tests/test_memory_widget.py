"""Memory browser: hierarchical name trees, search, and the Qt browser's
check trees / detachable sections, against a small in-memory fake of
`eco.elements.memory.Memory` (no disk, no hardware)."""
import types
from datetime import datetime

import pytest

from eco.widgets import memory_widget as mw


# ---------------------------------------------------------------------------
# pure model
# ---------------------------------------------------------------------------


def _labels(node):
    return [(c["label"], _labels(c)) if c["children"] else c["label"] for c in node["children"]]


def test_name_tree_groups_by_dotted_names_and_merges_single_chains():
    rows = [{"name": n} for n in [
        "mono.energy", "mono.theta", "slits.x.gap", "slits.x.center", "lonely.deep.leaf", "top",
    ]]
    tree = mw._name_tree(rows)
    assert _labels(tree) == [
        ("mono", ["energy", "theta"]),
        ("slits.x", ["gap", "center"]),  # "slits" had only one child: merged
        "lonely.deep.leaf",              # single deep row costs one line
        "top",
    ]
    slits = tree["children"][1]
    assert slits["path"] == "slits.x"
    assert [r["name"] for r in mw._tree_items(slits)] == ["slits.x.gap", "slits.x.center"]


def test_name_tree_node_can_be_row_and_group():
    rows = [{"name": "mot"}, {"name": "mot.offset"}, {"name": "mot.speed"}]
    (mot,) = mw._name_tree(rows)["children"]
    assert mot["item"] == {"name": "mot"}
    assert [c["label"] for c in mot["children"]] == ["offset", "speed"]
    assert [r["name"] for r in mw._tree_items(mot)] == ["mot", "mot.offset", "mot.speed"]


def test_search_needs_every_word_case_insensitive():
    assert mw._search_matches("", "anything")
    assert mw._search_matches("MONO en", "mono.energy")
    assert not mw._search_matches("mono gap", "mono.energy")
    assert mw._search_matches("align  run12", "before align", "run12")


def test_overview_search_covers_message_preset_group_date():
    row = {
        "key": "2026-11-01T10:00:00", "date": datetime(2026, 11, 1, 10, 0),
        "message": "aligned KB", "groups": ["settings"], "presetname": "kb_good",
    }
    for text in ["kb_good", "aligned", "settings", "2026-11-01", "KB GOOD"[:2]]:
        assert mw._overview_row_matches(row, text), text
    assert not mw._overview_row_matches(row, "mono")


def test_visible_rows_apply_changed_only_and_search():
    rows = [
        {"name": "a.x", "changed": True}, {"name": "a.y", "changed": False},
        {"name": "b.x", "changed": True},
    ]
    assert [r["name"] for r in mw._visible_detail_rows(rows, changed_only=True)] == ["a.x", "b.x"]
    assert [r["name"] for r in mw._visible_detail_rows(rows, search="a.")] == ["a.x", "a.y"]
    pairs = [("settings", "a.x", 1), ("display", "b.y", 2)]
    assert mw._visible_pick_items(pairs, "display") == [("display", "b.y", 2)]


# ---------------------------------------------------------------------------
# fake memory / assembly
# ---------------------------------------------------------------------------


class _Value:
    def __init__(self, v):
        self.v = v

    def get_current_value(self):
        return self.v


class _Node:
    """Attribute tree that `memory_widget`'s `name2obj` walks via __dict__."""


def _make_obj_tree(values):
    root = _Node()
    for name, v in values.items():
        parts = name.split(".")
        node = root
        for p in parts[:-1]:
            node = node.__dict__.setdefault(p, _Node())
        node.__dict__[parts[-1]] = _Value(v)
    return root


class FakeMemory:
    categories = {"recall": ["settings"], "track": []}

    def __init__(self, present, stored):
        self._parent = _make_obj_tree(present)
        self._present = present
        self._stored = stored  # {key: {"message":..., "settings": {...}}}
        self.recalled = []
        self.memorized = []

    def setup_path(self):
        pass

    def _memories(self):
        return {
            k: {"message": v["message"], "categories": {"recall": ["settings"]},
                "presetname": v.get("presetname")}
            for k, v in self._stored.items()
        }

    def ancestor_memories(self):
        return []

    def get_memory(self, key=None, input_obj=None):
        return {"settings": dict(self._stored[key]["settings"])}

    def entry_selection_names(self, mem):
        return list(mem)

    def get_recall_dict(self, mem, selection=None):
        out = {}
        for group, vals in mem.items():
            if selection is None or group in selection:
                out.update(vals)
        return out

    def obj_parent(self):
        return self._parent

    def get_memorize_candidates(self, selection=None):
        return [("settings", n, v) for n, v in self._present.items()]

    def recall(self, input_obj=None, force=False, selection=None):
        self.recalled.append(input_obj["settings"])

    def memorize(self, **kwargs):
        self.memorized.append(kwargs)


PRESENT = {"mono.energy": 1.0, "mono.theta": 2.0, "slits.gap": 3.0, "slits.center": 4.0, "att": 5.0}
STORED = {
    "2026-11-01T10:00:00": {
        "message": "old alignment",
        "settings": {"mono.energy": 9.0, "mono.theta": 8.0, "slits.gap": 7.0, "slits.center": 4.0, "att": 6.0},
    },
    "2026-11-02T10:00:00": {"message": "kb tuned", "presetname": "kb_good", "settings": {"att": 1.0}},
}


def _fake_assembly(memory):
    return types.SimpleNamespace(
        memory=memory, alias=types.SimpleNamespace(get_full_name=lambda: "fake_dev"),
    )


# ---------------------------------------------------------------------------
# Qt browser
# ---------------------------------------------------------------------------


@pytest.fixture
def qt_browser():
    pytest.importorskip("qtpy")
    from qtpy import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    memory = FakeMemory(PRESENT, STORED)
    browser = mw.MemoryBrowserQt(_fake_assembly(memory))
    browser.window.setAttribute(__import__("qtpy").QtCore.Qt.WA_DeleteOnClose, False)
    yield browser, memory, app
    browser.window.hide()
    browser.window.deleteLater()
    app.processEvents()


def _load(browser, message):
    from qtpy import QtCore

    for i in range(browser.list.topLevelItemCount()):
        item = browser.list.topLevelItem(i)
        if item.text(2) == message:
            browser._on_pick_stored(item)
            return
    raise AssertionError(f"no overview row {message!r}")


def _group_item(tree, label):
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if item.text(0) == label:
            return item
    raise AssertionError(f"no top-level {label!r} in {[tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]}")


def test_qt_overview_search_and_presets_only(qt_browser):
    browser, _memory, _app = qt_browser
    browser.overview_selection_combo.setCurrentText("all")
    assert browser.list.topLevelItemCount() == 2
    browser.overview_search.setText("kb_good")
    assert browser.list.topLevelItemCount() == 1
    assert browser.list.topLevelItem(0).text(1) == "kb_good"
    browser.overview_search.setText("")
    browser.presets_only_cb.setChecked(True)
    assert browser.list.topLevelItemCount() == 1


def test_qt_detail_tree_group_checkbox_and_search_limits_recall(qt_browser, monkeypatch):
    from qtpy import QtCore, QtWidgets

    browser, memory, _app = qt_browser
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
    )
    browser.overview_selection_combo.setCurrentText("all")
    _load(browser, "old alignment")
    # changed only: slits.center is unchanged and hidden
    mono = _group_item(browser.tree, "mono")
    assert mono.childCount() == 2
    assert mono.text(4) == "2 changed / 2"
    slits = _group_item(browser.tree, "slits.gap")  # one shown child -> merged single row
    assert slits.data(0, QtCore.Qt.UserRole) == "slits.gap"

    # unchecking the group unchecks both children
    mono.setCheckState(0, QtCore.Qt.Unchecked)
    assert browser._state["checked"]["mono.energy"] is False
    assert browser._state["checked"]["mono.theta"] is False
    mono.child(0).setCheckState(0, QtCore.Qt.Checked)
    assert mono.checkState(0) == QtCore.Qt.PartiallyChecked

    # a search hides rows; recall only sends checked *shown* rows
    browser.detail_search.setText("mono")
    browser._on_recall()
    assert memory.recalled[-1] == {"mono.energy": 9.0}

    browser.detail_search.setText("")
    browser._set_all_checked(True)
    browser._on_recall()
    assert set(memory.recalled[-1]) == {"mono.energy", "mono.theta", "slits.gap", "att"}


def test_qt_pick_items_tree_and_search_limits_save(qt_browser):
    browser, memory, _app = qt_browser
    assert not browser.docks["pick"].isVisibleTo(browser.window)
    browser.pick_items_cb.setChecked(True)
    assert browser.docks["pick"].isVisibleTo(browser.window)
    tree = browser.pick_items_list
    assert _group_item(tree, "mono").childCount() == 2
    browser.pick_search.setText("slits")
    browser.message_edit.setText("just slits")
    browser._on_save()
    assert memory.memorized[-1]["pick_items"] == ["slits.gap", "slits.center"]


def test_qt_sections_detach_and_attach(qt_browser):
    browser, _memory, app = qt_browser
    dock = browser.docks["detail"]
    browser._detach_buttons["detail"].click()
    app.processEvents()
    assert dock.isFloating()
    assert browser._detach_buttons["detail"].text() == "attach"
    assert dock.width() >= mw._FLOAT_MIN_SIZE["detail"][0]
    browser._detach_buttons["detail"].click()
    app.processEvents()
    assert not dock.isFloating()
    assert browser._detach_buttons["detail"].text() == "detach"


# ---------------------------------------------------------------------------
# ipywidgets browser
# ---------------------------------------------------------------------------


def _walk(widget):
    yield widget
    for child in getattr(widget, "children", ()) or ():
        yield from _walk(child)


def test_ipywidgets_search_tree_and_shown_only_recall():
    widgets = pytest.importorskip("ipywidgets")
    memory = FakeMemory(PRESENT, STORED)
    box = mw.make_memory_browser_ipywidgets(_fake_assembly(memory))
    texts = [w for w in _walk(box) if isinstance(w, widgets.Text) and not isinstance(w, widgets.Combobox)]
    overview_search = next(w for w in texts if "preset name" in w.placeholder)
    detail_search = next(w for w in texts if "mono energy" in w.placeholder)
    combo = next(w for w in _walk(box) if isinstance(w, widgets.Combobox))
    combo.value = "all"

    def overview_buttons():
        return [w for w in _walk(box) if isinstance(w, widgets.Button) and "10:00" in w.description]

    assert len(overview_buttons()) == 2
    overview_search.value = "kb_good"
    (btn,) = overview_buttons()
    overview_search.value = "alignment"
    overview_buttons()[0].click()

    accordions = [w for w in _walk(box) if isinstance(w, widgets.Accordion)]
    assert [a.get_title(0) for a in accordions] == ["mono (2)"]
    detail_search.value = "mono"
    recall = next(w for w in _walk(box) if isinstance(w, widgets.Button) and w.description == "Recall selected values")
    recall.click()
    assert memory.recalled[-1] == {"mono.energy": 9.0, "mono.theta": 8.0}


# ---------------------------------------------------------------------------
# Tk browser (needs a display)
# ---------------------------------------------------------------------------


def test_tk_search_hierarchy_and_shown_only_recall(monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    memory = FakeMemory(PRESENT, STORED)
    browser = mw.MemoryBrowserTk(_fake_assembly(memory), parent=root)
    try:
        browser.overview_selection_var.set("all")
        browser.overview_search_var.set("alignment")
        assert browser.overview_list.size() == 1
        browser.overview_list.selection_set(0)
        browser._on_pick_stored()
        headers = [w.cget("text") for w in browser.detail_inner.winfo_children() if not w.winfo_children()]
        assert headers == ["mono (2)"]
        browser.detail_search_var.set("slits")
        browser._on_recall()
        assert memory.recalled[-1] == {"slits.gap": 7.0}
    finally:
        root.destroy()
