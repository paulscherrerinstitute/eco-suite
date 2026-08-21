"""get_display_str()'s show_triggers option: AdjustableTrigger rows
("(trigger)"/a lone action button) are meant for a clickable panel (Qt or
Jupyter widget), not the plain terminal repr / elog HTML table -- a row
that can't be clicked and has no value to show is just noise there."""
from eco.elements.adjustable import AdjustableTrigger
from eco.elements.assembly import Assembly
from eco.aliases.aliases import Alias


class _FakeDetector:
    def __init__(self, name, value=0.0):
        self.name = name
        self.alias = Alias(name)
        self._value = value

    def get_current_value(self):
        return self._value


def _assembly_with(items):
    asm = Assembly(name="slit_und")
    asm.status_collection.get_list = lambda selection=None, **kw: list(items)
    return asm


def test_get_display_str_hides_triggers_by_default():
    detector = _FakeDetector("hpos", 0.1002)
    trigger = AdjustableTrigger(lambda: None, name="home_all_blades")
    asm = _assembly_with([detector, trigger])

    s = asm.get_display_str()

    assert "hpos" in s
    assert "home_all_blades" not in s
    assert "(trigger)" not in s


def test_get_display_str_show_triggers_true_includes_them():
    detector = _FakeDetector("hpos", 0.1002)
    trigger = AdjustableTrigger(lambda: None, name="home_all_blades")
    asm = _assembly_with([detector, trigger])

    s = asm.get_display_str(show_triggers=True)

    assert "hpos" in s
    assert "home_all_blades" in s
    assert "(trigger)" in s


def test_repr_hides_triggers_same_as_get_display_str_default():
    trigger = AdjustableTrigger(lambda: None, name="home_all_blades")
    asm = _assembly_with([trigger])

    assert "home_all_blades" not in repr(asm)


def test_only_triggers_produces_an_empty_table_not_a_crash():
    trigger = AdjustableTrigger(lambda: None, name="home_all_blades")
    asm = _assembly_with([trigger])

    assert asm.get_display_str() == ""
