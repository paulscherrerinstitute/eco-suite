"""eco.ipymagic -- the `...`-triggered inline component picker.

Qt itself (the actual picker window) is not exercised here -- these tests
monkeypatch eco.widgets.component_selector_qt.pick_component_modal (the one
function ipymagic._EllipsisPicker calls) and check the pure-AST behavior:
which `...` occurrences get replaced, in what order, with what, and which
are correctly left alone.
"""
import ast

import pytest

from eco import ipymagic


class _FakeEvents:
    def register(self, *a, **k):
        pass


class _FakeShell:
    def __init__(self):
        self.ast_transformers = []


@pytest.fixture(autouse=True)
def _reset_state():
    ipymagic._state.clear()
    yield
    ipymagic._state.clear()


def _transform_source(source, root="the_root"):
    tree = ast.parse(source)
    picker = ipymagic._EllipsisPicker(root, "All", None, None)
    new_tree = picker.visit(tree)
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree), picker.picked_any


def test_bare_ellipsis_expression_is_replaced_with_the_picked_path(monkeypatch):
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        lambda root, **kw: ("bernina.mono.energy", object()),
    )
    out, picked_any = _transform_source("(1 / ...).plot()")
    assert out == "(1 / bernina.mono.energy).plot()"
    assert picked_any is True


def test_two_markers_are_resolved_left_to_right_in_source_order(monkeypatch):
    picks = iter(["bernina.mono.energy", "bernina.attenuator.transmission"])
    seen_roots = []

    def fake_pick(root, **kw):
        seen_roots.append(root)
        return next(picks), object()

    monkeypatch.setattr("eco.widgets.component_selector_qt.pick_component_modal", fake_pick)
    out, picked_any = _transform_source(
        "cen = (left := ...) - (right := ...)", root="bernina"
    )
    assert out == "cen = (left := bernina.mono.energy) - (right := bernina.attenuator.transmission)"
    assert picked_any is True
    assert seen_roots == ["bernina", "bernina"]


def test_subscript_slice_ellipsis_is_left_alone(monkeypatch):
    def fail_if_called(root, **kw):
        raise AssertionError("picker should not be invoked for arr[...]")

    monkeypatch.setattr("eco.widgets.component_selector_qt.pick_component_modal", fail_if_called)
    out, picked_any = _transform_source("arr[...].sum()")
    assert out == "arr[...].sum()"
    assert picked_any is False


def test_cancelled_pick_leaves_the_literal_ellipsis_in_place(monkeypatch, capsys):
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        lambda root, **kw: (None, None),
    )
    out, picked_any = _transform_source("(1 / ...).plot()")
    assert out == "(1 / ...).plot()"
    assert picked_any is False
    assert "cancelled" in capsys.readouterr().out


def test_transform_is_a_noop_when_start_has_not_been_called():
    # ipymagic._state is empty (autouse fixture clears it) -- _transform
    # itself (not just the picker) must not try to pick anything.
    tree = ast.parse("(1 / ...).plot()")
    out = ast.unparse(ipymagic._ast_transformer.visit(tree))
    assert out == "(1 / ...).plot()"


def test_start_registers_the_transformer_once(monkeypatch):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)

    ipymagic.start("bernina")
    ipymagic.start("bernina")  # idempotent -- must not install a second hook

    assert shell.ast_transformers == [ipymagic._ast_transformer]
    assert ipymagic._state["root"] == "bernina"


def test_start_raises_without_an_ipython_session(monkeypatch):
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    with pytest.raises(RuntimeError):
        ipymagic.start("bernina")


def test_stop_makes_the_transform_a_noop_again(monkeypatch):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)
    ipymagic.start("bernina")

    ipymagic.stop()

    tree = ast.parse("(1 / ...).plot()")
    out = ast.unparse(ipymagic._ast_transformer.visit(tree))
    assert out == "(1 / ...).plot()"
