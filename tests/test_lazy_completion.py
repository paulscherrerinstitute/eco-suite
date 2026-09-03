"""Tests for the two-Tab completion-confirmation gate in
eco.utilities.lazy_completion -- see CLAUDE.md's "Tab completion" section
for the full mechanism this sits on top of.

Uses a minimal fake stand-in for IPCompleter (exposing only the attributes
`_gated_attr_matches` actually touches: `_ATTR_MATCH_RE`, `namespace`,
`global_namespace`, `_strip_code_before_operator`) rather than a real
IPython InteractiveShell/IPCompleter, so these tests stay fast and isolated
from IPython's global singleton state.
"""

import re

import pytest

from eco.utilities import lazy_completion
from eco.utilities.config import Proxy


class _FakeCompleter:
    _ATTR_MATCH_RE = re.compile(r"(.+)\.(\w*)$")

    def __init__(self, namespace=None, global_namespace=None):
        self.namespace = namespace or {}
        self.global_namespace = global_namespace or {}

    def _strip_code_before_operator(self, expr):
        return expr


class Line:
    def __init__(self):
        self.gauge = "a-gauge"

    def pump_down(self):
        return "pumping"


class System:
    def __init__(self):
        self.line1_usd = Line()


@pytest.fixture(autouse=True)
def _clean_armed_state():
    lazy_completion._armed_proxy_ids.clear()
    yield
    lazy_completion._armed_proxy_ids.clear()


# -- _is_resolved --------------------------------------------------------


def test_is_resolved_true_for_a_plain_object():
    assert lazy_completion._is_resolved(System()) is True


def test_is_resolved_false_for_a_cold_proxy_without_triggering_it():
    built = []
    proxy = Proxy(lambda: built.append(1) or System())
    assert lazy_completion._is_resolved(proxy) is False
    assert built == []  # merely checking must not resolve it


def test_is_resolved_true_for_a_touched_proxy():
    proxy = Proxy(System)
    _ = proxy.line1_usd
    assert lazy_completion._is_resolved(proxy) is True


# -- _find_blocking_unresolved --------------------------------------------


def test_find_blocking_unresolved_none_when_name_missing():
    assert lazy_completion._find_blocking_unresolved("prepump", {}, {}) is None


def test_find_blocking_unresolved_returns_the_cold_proxy_itself():
    proxy = Proxy(System)
    name, obj = lazy_completion._find_blocking_unresolved(
        "prepump", {"prepump": proxy}, {}
    )
    assert name == "prepump"
    assert obj is proxy


def test_find_blocking_unresolved_stops_at_a_cold_proxy_deeper_in_the_chain():
    inner_built = []
    inner_proxy = Proxy(lambda: inner_built.append(1) or Line())

    class Outer:
        pass

    outer = Outer()
    outer.inner = inner_proxy

    name, obj = lazy_completion._find_blocking_unresolved(
        "outer.inner", {"outer": outer}, {}
    )
    assert name == "inner"
    assert obj is inner_proxy
    assert inner_built == []  # walking to find it must not resolve it


def test_find_blocking_unresolved_none_when_chain_is_fully_resolved():
    proxy = Proxy(System)
    _ = proxy.line1_usd  # touch it so it's resolved
    assert (
        lazy_completion._find_blocking_unresolved(
            "prepump.line1_usd", {"prepump": proxy}, {}
        )
        is None
    )


def test_find_blocking_unresolved_falls_back_to_global_namespace():
    proxy = Proxy(System)
    name, obj = lazy_completion._find_blocking_unresolved(
        "prepump", {}, {"prepump": proxy}
    )
    assert (name, obj) == ("prepump", proxy)


# -- _gated_attr_matches ---------------------------------------------------


def test_first_tab_into_a_cold_component_blocks_without_resolving(monkeypatch, capsys):
    built = []
    proxy = Proxy(lambda: built.append(1) or System())
    completer = _FakeCompleter(namespace={"prepump": proxy})

    def fail_if_called(*a, **kw):
        raise AssertionError("must not fall through to the real matcher yet")

    monkeypatch.setattr(lazy_completion, "_orig_attr_matches", fail_if_called)

    result = lazy_completion._gated_attr_matches(completer, "prepump.line1_usd.p")

    assert result == ([], "")
    assert built == []
    assert "not initialized yet" in capsys.readouterr().out
    assert id(proxy) in lazy_completion._armed_proxy_ids


def test_second_tab_resolves_and_falls_through_to_the_real_matcher(monkeypatch, capsys):
    built = []
    proxy = Proxy(lambda: built.append(1) or System())
    completer = _FakeCompleter(namespace={"prepump": proxy})
    lazy_completion._armed_proxy_ids.add(id(proxy))

    calls = []

    def fake_orig(self, text, include_prefix=True, context=None):
        calls.append((self, text, include_prefix, context))
        return ["prepump.line1_usd.pump_down"], ".p"

    monkeypatch.setattr(lazy_completion, "_orig_attr_matches", fake_orig)

    result = lazy_completion._gated_attr_matches(completer, "prepump.line1_usd.p")

    assert built == [1]  # now actually resolved
    assert id(proxy) not in lazy_completion._armed_proxy_ids
    assert calls == [(completer, "prepump.line1_usd.p", True, None)]
    assert result == (["prepump.line1_usd.pump_down"], ".p")
    assert capsys.readouterr().out == ""


def test_gate_falls_through_untouched_for_an_already_resolved_component(monkeypatch):
    proxy = Proxy(System)
    _ = proxy.line1_usd  # resolve up front, like a component already in use
    completer = _FakeCompleter(namespace={"prepump": proxy})

    def fake_orig(self, text, include_prefix=True, context=None):
        return ["sentinel"], ".p"

    monkeypatch.setattr(lazy_completion, "_orig_attr_matches", fake_orig)

    result = lazy_completion._gated_attr_matches(completer, "prepump.line1_usd.p")

    assert result == (["sentinel"], ".p")
    assert lazy_completion._armed_proxy_ids == set()


def test_gate_ignores_expressions_that_are_not_plain_dotted_names(monkeypatch):
    """Calls/subscripts/etc. bail out to IPython's normal handling instead
    of the gate trying (and likely failing) to reason about them."""
    completer = _FakeCompleter(namespace={})

    def fake_orig(self, text, include_prefix=True, context=None):
        return ["sentinel"], ".p"

    monkeypatch.setattr(lazy_completion, "_orig_attr_matches", fake_orig)

    result = lazy_completion._gated_attr_matches(completer, "foo().p")

    assert result == (["sentinel"], ".p")


def test_first_tab_reports_and_does_not_raise_if_initialization_fails(monkeypatch, capsys):
    def boom():
        raise RuntimeError("EPICS timeout")

    proxy = Proxy(boom)
    completer = _FakeCompleter(namespace={"prepump": proxy})
    lazy_completion._armed_proxy_ids.add(id(proxy))

    def fail_if_called(*a, **kw):
        raise AssertionError("must not fall through when initialization failed")

    monkeypatch.setattr(lazy_completion, "_orig_attr_matches", fail_if_called)

    result = lazy_completion._gated_attr_matches(completer, "prepump.p")

    assert result == ([], "")
    assert "failed to initialize" in capsys.readouterr().out
    assert id(proxy) not in lazy_completion._armed_proxy_ids


# -- install_lazy_completion_gate ------------------------------------------


def test_install_is_idempotent(monkeypatch):
    from IPython.core.completer import IPCompleter

    monkeypatch.setattr(lazy_completion, "_installed", False)
    original = IPCompleter._attr_matches
    try:
        lazy_completion.install_lazy_completion_gate()
        patched_once = IPCompleter._attr_matches
        lazy_completion.install_lazy_completion_gate()
        assert IPCompleter._attr_matches is patched_once
        assert patched_once is lazy_completion._gated_attr_matches
    finally:
        IPCompleter._attr_matches = original
        monkeypatch.setattr(lazy_completion, "_installed", False)
