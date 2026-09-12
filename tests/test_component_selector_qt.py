"""eco.widgets.component_selector_qt -- the confirm-gated two-tab picker
(ComponentSelectorQt's require_confirm mode, CommandSelectorQt, and
PickerQtWindow) used by eco.ipymagic's Qt-modal fallback.

Real Qt widgets are built (qtpy needs a QApplication instance either way),
but nothing is actually shown/rendered or driven via real mouse/keyboard
events -- these call the same handler methods a click/Enter would trigger,
same convention as eco.ipymagic's own tests driving _EllipsisPicker
directly rather than a real picker window.
"""
import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

from eco.elements.adjustable import DummyAdjustable
from eco.elements.assembly import Assembly
from eco.widgets.component_selector_qt import CommandSelectorQt, ComponentSelectorQt


_app_ref = None  # keep a strong reference -- see component_selector_qt.py's own _app_ref


def _app():
    global _app_ref
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _app_ref = app
    return app


class _Sub(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(DummyAdjustable, name="energy")


class _Root(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(_Sub, name="mono")


def _first_leaf_item(tree):
    """First top-level item's first child (mono -> energy), expanding as
    needed -- ComponentSelectorQt.refresh() already expandToDepth(0)s the
    top level, so the child rows exist without a manual expand click."""
    top = tree.topLevelItem(0)
    return top.child(0) if top.childCount() else top


def test_value_column_is_gone():
    _app()
    sel = ComponentSelectorQt(_Root(name="root"))
    assert [sel.tree.headerItem().text(i) for i in range(sel.tree.columnCount())] == [
        "Name",
        "Type",
    ]


def test_default_mode_selects_immediately_on_click():
    # back-compat: scan_launcher_qt.py's ComponentPickerQt dialog relies on
    # a plain click firing on_select right away to enable its own OK
    # button -- require_confirm defaults to False so that keeps working.
    _app()
    sel = ComponentSelectorQt(_Root(name="root"))
    picked = []
    sel.on_select(lambda path, obj: picked.append(path))

    item = _first_leaf_item(sel.tree)
    sel._on_item_clicked(item, 0)

    assert picked == ["mono.energy"]


def test_require_confirm_mode_a_click_only_highlights():
    _app()
    sel = ComponentSelectorQt(_Root(name="root"), require_confirm=True)
    picked = []
    sel.on_select(lambda path, obj: picked.append(path))

    item = _first_leaf_item(sel.tree)
    sel._on_item_clicked(item, 0)

    assert picked == []
    assert sel.get_selected() == (None, None)


def test_require_confirm_mode_confirm_commits_the_pending_click():
    _app()
    sel = ComponentSelectorQt(_Root(name="root"), require_confirm=True)
    picked = []
    sel.on_select(lambda path, obj: picked.append(path))

    sel._on_item_clicked(_first_leaf_item(sel.tree), 0)
    committed = sel.confirm()

    assert committed is True
    assert picked == ["mono.energy"]


def test_require_confirm_mode_confirm_is_a_noop_with_nothing_pending():
    _app()
    sel = ComponentSelectorQt(_Root(name="root"), require_confirm=True)
    picked = []
    sel.on_select(lambda path, obj: picked.append(path))

    assert sel.confirm() is False
    assert picked == []


def test_item_activated_always_confirms_directly_regardless_of_mode():
    # double-click / Enter-on-focused-item is a deliberate action either
    # way -- itemActivated always confirms outright, no separate Go/Enter
    # step needed even in require_confirm mode.
    _app()
    sel = ComponentSelectorQt(_Root(name="root"), require_confirm=True)
    picked = []
    sel.on_select(lambda path, obj: picked.append(path))

    sel._on_item_activated(_first_leaf_item(sel.tree), 0)

    assert picked == ["mono.energy"]


def test_require_confirm_hides_the_per_row_go_buttons():
    _app()
    sel = ComponentSelectorQt(_Root(name="root"), require_confirm=True)
    assert sel.recent_goto_btn.isHidden()
    assert sel.bookmark_goto_btn.isHidden()


def test_default_mode_keeps_the_per_row_go_buttons_visible():
    _app()
    sel = ComponentSelectorQt(_Root(name="root"), require_confirm=False)
    assert not sel.recent_goto_btn.isHidden()
    assert not sel.bookmark_goto_btn.isHidden()


# -- CommandSelectorQt -------------------------------------------------------


def test_command_selector_lists_recent_then_recommended(monkeypatch):
    _app()
    monkeypatch.setattr("eco.logs.recent_commands", lambda n: ["mono.mv(5)"])
    monkeypatch.setattr(
        "eco.logs.frequent_commands", lambda n: ["daq.compare_channels()", "mono.mv(5)"]
    )

    cs = CommandSelectorQt()

    assert [cs.list.item(i).data(QtCore.Qt.UserRole) for i in range(cs.list.count())] == [
        "mono.mv(5)",
        "daq.compare_channels()",
    ]


def test_command_selector_click_only_highlights_confirm_commits(monkeypatch):
    _app()
    monkeypatch.setattr("eco.logs.recent_commands", lambda n: ["mono.mv(5)"])
    monkeypatch.setattr("eco.logs.frequent_commands", lambda n: [])

    cs = CommandSelectorQt()
    picked = []
    cs.on_select(lambda text, obj: picked.append(text))

    cs._on_item_clicked(cs.list.item(0))
    assert picked == []

    assert cs.confirm() is True
    assert picked == ["mono.mv(5)"]
    assert cs.get_selected() == ("mono.mv(5)", None)


def test_command_selector_item_activated_confirms_directly(monkeypatch):
    _app()
    monkeypatch.setattr("eco.logs.recent_commands", lambda n: ["mono.mv(5)"])
    monkeypatch.setattr("eco.logs.frequent_commands", lambda n: [])

    cs = CommandSelectorQt()
    picked = []
    cs.on_select(lambda text, obj: picked.append(text))

    cs._on_item_activated(cs.list.item(0))

    assert picked == ["mono.mv(5)"]
