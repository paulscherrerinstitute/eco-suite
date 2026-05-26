import pytest

from eco.utilities.config import Namespace


class BadThing:
    def __init__(self, value, name=None):
        raise ValueError("boom")


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
