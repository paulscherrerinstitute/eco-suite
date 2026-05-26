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
