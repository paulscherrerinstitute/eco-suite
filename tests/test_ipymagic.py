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


class _FakeCompleter:
    def __init__(self):
        self.custom_matchers = []


class _FakeShell:
    def __init__(self):
        self.ast_transformers = []
        self.Completer = _FakeCompleter()


@pytest.fixture(autouse=True)
def _reset_state():
    ipymagic._state.clear()
    yield
    ipymagic._state.clear()


class _NamedRoot:
    """A pick_component_modal path is relative to root (see
    ipymagic._full_path's docstring) -- these tests need a `root` with a
    real `.name` for the resulting expanded code to come out right, not
    a bare string (which has no `.name` and would fall back to a dummy
    "root" prefix)."""

    def __init__(self, name):
        self.name = name


def _transform_source(source, root=None):
    if root is None:
        root = _NamedRoot("bernina")
    tree = ast.parse(source)
    picker = ipymagic._EllipsisPicker(root, "All", None, None)
    new_tree = picker.visit(tree)
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree), picker.picked_any


def test_bare_ellipsis_expression_is_replaced_with_the_picked_path(monkeypatch):
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        # relative to root, like the real picker
        lambda root, **kw: ("component", "mono.energy", object()),
    )
    out, picked_any = _transform_source("(1 / ...).plot()")
    assert out == "(1 / bernina.mono.energy).plot()"
    assert picked_any is True


def test_two_markers_are_resolved_left_to_right_in_source_order(monkeypatch):
    picks = iter(["mono.energy", "attenuator.transmission"])
    seen_roots = []

    def fake_pick(root, **kw):
        seen_roots.append(root)
        return "component", next(picks), object()

    monkeypatch.setattr("eco.widgets.component_selector_qt.pick_component_modal", fake_pick)
    root = _NamedRoot("bernina")
    out, picked_any = _transform_source(
        "cen = (left := ...) - (right := ...)", root=root
    )
    assert out == "cen = (left := bernina.mono.energy) - (right := bernina.attenuator.transmission)"
    assert picked_any is True
    assert seen_roots == [root, root]


def test_command_pick_is_used_verbatim_not_prefixed_with_root(monkeypatch):
    # a "command" pick is already a full expression string -- unlike a
    # "component" pick (a bare dotted path relative to root), it must not
    # be run through _full_path.
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        lambda root, **kw: ("command", "mono.mv(5)", None),
    )
    out, picked_any = _transform_source("...")
    assert out == "mono.mv(5)"
    assert picked_any is True


def test_subscript_slice_ellipsis_is_left_alone(monkeypatch):
    def fail_if_called(root, **kw):
        raise AssertionError("picker should not be invoked for arr[...]")

    monkeypatch.setattr("eco.widgets.component_selector_qt.pick_component_modal", fail_if_called)
    out, picked_any = _transform_source("arr[...].sum()")
    assert out == "arr[...].sum()"
    assert picked_any is False


def test_qt_modal_fallback_is_attempted_in_a_real_terminal_session(monkeypatch):
    # a terminal session alone must no longer skip the Qt modal -- only the
    # absence of a display does (see the next test).
    monkeypatch.setattr(ipymagic, "_has_display", lambda: True)
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        lambda root, **kw: ("component", "mono.energy", object()),
    )

    out, picked_any = _transform_source("(1 / ...).plot()")

    assert out == "(1 / bernina.mono.energy).plot()"
    assert picked_any is True


def test_qt_modal_fallback_is_skipped_without_a_display(monkeypatch, capsys):
    monkeypatch.setattr(ipymagic, "_has_display", lambda: False)

    def fail_if_called(root, **kw):
        raise AssertionError("the Qt modal must not be opened with no display available")

    monkeypatch.setattr("eco.widgets.component_selector_qt.pick_component_modal", fail_if_called)

    out, picked_any = _transform_source("(1 / ...).plot()")

    assert out == "(1 / ...).plot()"
    assert picked_any is False
    assert "Tab" in capsys.readouterr().out


def test_cancelled_pick_leaves_the_literal_ellipsis_in_place(monkeypatch, capsys):
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        lambda root, **kw: (None, None, None),
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


def test_ast_transformer_cancels_execution_and_fills_next_input(monkeypatch):
    # _EllipsisPicker alone (exercised above via _transform_source) only
    # checks *what* gets spliced in -- this checks _AstTransformer.visit's
    # own behavior once something was picked: the cell must not actually
    # run (empty body), and the expanded code must go to the next prompt
    # via set_next_input rather than being printed-then-executed.
    ipymagic._state["root"] = _NamedRoot("bernina")
    ipymagic._state["kind_filter"] = "All"
    ipymagic._state["bookmarks"] = None
    ipymagic._state["recent"] = None
    monkeypatch.setattr(
        "eco.widgets.component_selector_qt.pick_component_modal",
        lambda root, **kw: ("component", "mono.energy", object()),
    )
    next_inputs = []

    class _FakeIp:
        def set_next_input(self, text, replace=False):
            next_inputs.append((text, replace))

    monkeypatch.setattr("IPython.get_ipython", lambda: _FakeIp())

    tree = ast.parse("(1 / ...).plot()")
    new_tree = ipymagic._ast_transformer.visit(tree)

    assert new_tree.body == []
    assert next_inputs == [("(1 / bernina.mono.energy).plot()", True)]


def test_start_registers_the_transformer_and_matchers_once(monkeypatch):
    shell = _FakeShell()
    monkeypatch.setattr("IPython.get_ipython", lambda: shell)

    ipymagic.start("bernina")
    ipymagic.start("bernina")  # idempotent -- must not install a second hook

    assert shell.ast_transformers == [ipymagic._ast_transformer]
    assert shell.Completer.custom_matchers == [
        ipymagic._component_matcher,
        ipymagic._command_matcher,
    ]
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


# -- Tab-completion overlay (_component_matcher / _command_matcher) --------


class _Ctx:
    """Just enough of CompletionContext for these matchers -- both only
    ever read `.token`."""

    def __init__(self, token):
        self.token = token


class _FakeRoot:
    name = "bernina"


def _texts(result):
    return [c.text for c in result["completions"]]


def _types(result):
    return [c.type for c in result["completions"]]


def test_component_matcher_returns_nothing_when_not_started():
    assert ipymagic._component_matcher(_Ctx("...")) == {"completions": []}


def test_component_matcher_ignores_tokens_without_the_trigger(monkeypatch):
    ipymagic._state["root"] = _FakeRoot()
    assert ipymagic._component_matcher(_Ctx("bernina")) == {"completions": []}


def test_component_matcher_defers_to_command_matcher_for_the_history_trigger(monkeypatch):
    ipymagic._state["root"] = _FakeRoot()
    assert ipymagic._component_matcher(_Ctx("...h")) == {"completions": []}


def _fake_node(name, children=()):
    return type("N", (), {"name": name, "children": list(children)})()


def _fake_tree_relative_to_bernina():
    # build_tree's own node names are relative to root (see _full_path's
    # docstring) -- root's own name comes back "" (Alias.get_full_name
    # stops at base), children are unprefixed ("mono.energy", not
    # "bernina.mono.energy").
    return _fake_node(
        "",
        children=[
            _fake_node("mono.energy"),
            _fake_node("attenuator.transmission"),
        ],
    )


def test_component_matcher_sections_are_recent_then_recommended_then_namespace(monkeypatch):
    ipymagic._state["root"] = _FakeRoot()
    monkeypatch.setattr(
        "eco.elements.recent.RecentComponents.all", lambda self: ["bernina.slit1.width"]
    )
    monkeypatch.setattr(
        "eco.elements.recent.FrequencyCounts.top",
        lambda self, n: ["bernina.slit1.width", "bernina.mono.energy"],
    )
    monkeypatch.setattr(
        "eco.widgets.component_selector.build_tree",
        lambda root: _fake_tree_relative_to_bernina(),
    )

    result = ipymagic._component_matcher(_Ctx("..."))

    # bernina.slit1.width: recent, wins over also being "recommended".
    # bernina.mono.energy: recommended (already absolute -- RecentComponents/
    # FrequencyCounts paths come from Alias.get_full_name() with no base,
    # unlike build_tree's root-relative ones) -- also excluded from the
    # namespace section below since it's already shown once.
    # bernina.attenuator.transmission: plain namespace, root-prefixed since
    # build_tree gave back the relative "attenuator.transmission".
    assert _texts(result) == [
        "bernina.slit1.width",
        "bernina.mono.energy",
        "bernina.attenuator.transmission",
    ]
    assert _types(result) == ["recent", "recommended", "namespace"]
    assert result["suppress"] is True


def test_component_matcher_query_narrows_namespace_but_not_recent(monkeypatch):
    ipymagic._state["root"] = _FakeRoot()
    monkeypatch.setattr(
        "eco.elements.recent.RecentComponents.all", lambda self: ["bernina.slit1.width"]
    )
    monkeypatch.setattr("eco.elements.recent.FrequencyCounts.top", lambda self, n: [])
    monkeypatch.setattr(
        "eco.widgets.component_selector.build_tree",
        lambda root: _fake_tree_relative_to_bernina(),
    )

    result = ipymagic._component_matcher(_Ctx("...ene"))

    # "slit1.width" doesn't match "ene" but stays -- Recent is unfiltered.
    # Of the namespace section, only "bernina.mono.energy" (via "ene") matches.
    assert _texts(result) == ["bernina.slit1.width", "bernina.mono.energy"]


def test_command_matcher_returns_nothing_when_not_started():
    assert ipymagic._command_matcher(_Ctx("...h")) == {"completions": []}


def test_command_matcher_ignores_the_plain_component_trigger(monkeypatch):
    ipymagic._state["root"] = _FakeRoot()
    assert ipymagic._command_matcher(_Ctx("...")) == {"completions": []}


def test_command_matcher_has_recent_and_recommended_but_no_namespace_section(monkeypatch):
    ipymagic._state["root"] = _FakeRoot()
    monkeypatch.setattr("eco.logs.recent_commands", lambda n: ["mono.mv(5)"])
    monkeypatch.setattr(
        "eco.logs.frequent_commands", lambda n: ["daq.compare_channels()", "mono.mv(5)"]
    )

    result = ipymagic._command_matcher(_Ctx("...h"))

    assert _texts(result) == ["mono.mv(5)", "daq.compare_channels()"]
    assert _types(result) == ["recent", "recommended"]
