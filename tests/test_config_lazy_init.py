import pytest

from eco.utilities.config import Component, Namespace


class BadThing:
    def __init__(self, value, name=None):
        raise ValueError("boom")


class DependencyThing:
    def __init__(self, name=None):
        self.name = name


class NeedsDependency:
    def __init__(self, dependency, name=None):
        self.dependency = dependency


def test_lazy_init_failure_includes_manual_instantiation_string():
    ns = Namespace(name="test")
    ns.append_obj(BadThing, 1, lazy=True, name="bad")

    with pytest.raises(ValueError) as exc_info:
        ns.init_name("bad", raise_errors=True)

    assert exc_info.value.args[0] == "boom"

    manual = exc_info.value.args[-1]
    assert manual.startswith("Manual instantiation attempt:")
    assert "from test_config_lazy_init import BadThing" in manual
    assert manual == (
        "Manual instantiation attempt: "
        "from test_config_lazy_init import BadThing; "
        "BadThing(1, name='bad')"
    )
    assert ns.failed_items_excpetion["bad"].args[-1] == manual


def test_append_obj_from_config_resolves_component_dependencies_eagerly_for_non_lazy_nodes():
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")

    ns.append_obj_from_config(
        {
            "type": f"{__name__}:NeedsDependency",
            "name": "needs_dependency",
            "args": [Component("dependency")],
            "kwargs": {},
            "lazy": False,
        }
    )

    root = ns.initialized_items["needs_dependency"]
    assert isinstance(root.dependency, DependencyThing)
    assert root.dependency is not ns.get_obj("dependency")
    assert root.dependency.name == "dependency"


def test_init_all_retry_loop_is_bounded_by_n_cycles():
    """A name that reports "already initializing" on every attempt used to
    be retried for ever: the loop only stops when the set of such names is
    empty, and a build slower than init_timeout re-raises it every round.
    Observed live as a status-server init sitting at 76/87 for >10 minutes.
    """
    from eco.utilities.config import IsInitialisingError

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="stuck")

    calls = []

    def always_initialising(name, verbose=True, raise_errors=False, quiet=False):
        calls.append(name)
        raise IsInitialisingError(f"{name} is being initialized elsewhere")

    ns.init_name = always_initialising
    ns.init_all(
        required_only=False,
        background=False,
        silent=True,
        N_cycles=3,
        giveup_failed=False,
    )

    assert calls == ["stuck"] * 3


def test_resolve_item_does_not_build_a_lazy_item():
    """`lazy_items.get(n) or failed_items.get(n)` evaluated the proxy's
    truthiness, which resolves it - so merely looking a name up built the
    device."""
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")

    item = ns.resolve_item("dependency")

    assert item is not None
    assert ns.initialized_names == set(), "looking the name up initialized it"
    assert "dependency" in ns.lazy_names


def test_resolve_item_of_a_failed_name_does_not_reraise():
    """Same bug, worse symptom: for a previously-failed item the truthiness
    check re-raised its stored exception, so reinitialize() blew up on the
    very failure it was called to clear."""
    ns = Namespace(name="test")
    ns.append_obj(BadThing, 1, lazy=True, name="bad")
    # the state init_all's giveup_failed leaves behind: still an unresolved
    # proxy, moved into failed_items, with the failure recorded
    ns.failed_items["bad"] = ns.lazy_items.pop("bad")
    ns.failed_items_excpetion["bad"] = ValueError("boom")

    assert "bad" in ns.failed_names
    assert ns.resolve_item("bad") is not None


def test_reinitialize_clears_a_failed_name():
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")
    ns.failed_items["dependency"] = ns.lazy_items.pop("dependency")
    ns.failed_items_excpetion["dependency"] = ValueError("was broken")

    results = ns.reinitialize("dependency", verbose=False)

    assert results == {"dependency": True}
    assert "dependency" in ns.initialized_names


def test_manual_instantiation_hint_renders_an_unresolved_proxy_as_a_placeholder():
    from eco.utilities.config import format_manual_instantiation

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")
    proxy = ns.resolve_item("dependency")

    text = format_manual_instantiation(DependencyThing, [proxy], {}, name="x")

    assert "not yet initialized" in text
    assert ns.initialized_names == set(), "building the hint initialized the proxy"
