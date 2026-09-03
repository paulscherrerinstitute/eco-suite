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


# --- tree= (name column as an indented tree instead of repeated prefixes) ---


class _FakeNested(_FakeDetector):
    """A detector whose alias reports a *dotted* name, i.e. what a
    recursively unfolded (`is_display="recursive"`) sub-assembly contributes
    to its parent's display table."""

    def __init__(self, full_name, value=0.0):
        super().__init__(full_name.split(".")[-1], value)
        self.alias.get_full_name = lambda base=None, _n=full_name: _n


def _nested_assembly():
    return _assembly_with(
        [
            _FakeNested("ver.x", 0.1),
            _FakeNested("ver.y", 0.2),
            _FakeNested("hor.x", 0.3),
            _FakeNested("hor.y", 0.4),
            _FakeDetector("mode", 0),
        ]
    )


def _plain(s):
    """Strip rich's ANSI styling so row text can be matched directly."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_flat_names_are_the_default():
    s = _plain(_nested_assembly().get_display_str())

    assert "ver.x" in s
    assert "├──" not in s


def test_tree_true_drops_the_repeated_prefix_and_indents():
    s = _plain(_nested_assembly().get_display_str(tree=True))
    lines = [l.strip() for l in s.splitlines() if l.strip()]

    assert "ver.x" not in s and "hor.y" not in s
    # one header row per sub-assembly, its properties indented below it
    assert any(l.startswith("ver") for l in lines)
    assert any(l.startswith("├── x") for l in lines)
    assert any(l.startswith("└── y") for l in lines)
    # rows themselves are untouched: same values, same count (+2 headers)
    assert "0.1" in s and "0.4" in s
    assert len(lines) == 5 + 2
    # a name with no dots stays a plain top-level row
    assert any(l.startswith("mode") for l in lines)


def test_tree_defaults_to_eco_defaults_display_tree():
    import eco.defaults as defaults

    asm = _nested_assembly()
    old = defaults.DISPLAY_TREE
    try:
        defaults.DISPLAY_TREE = True
        assert "├──" in _plain(asm.get_display_str())
        assert "├──" in _plain(repr(asm))
        # explicit argument still wins over the global default
        assert "ver.x" in _plain(asm.get_display_str(tree=False))
    finally:
        defaults.DISPLAY_TREE = old


def test_per_object_display_tree_attribute_wins_over_the_global_default():
    asm = _nested_assembly()
    asm._display_tree = True

    assert "├──" in _plain(asm.get_display_str())
    assert "├──" in _plain(asm._display_text())


def test_tree_groups_stay_colour_blocked_per_sub_assembly():
    """Each sub-assembly (header row included) keeps its own background
    shade, as in the flat layout -- the tree only changes the name column."""
    import re

    s = _nested_assembly().get_display_str(tree=True)
    lines = [l for l in s.splitlines() if _plain(l).strip()]
    # 'ver' + its 2 children in one palette colour, 'hor' + its 2 in another

    def _bg(line):
        return re.findall(r"\x1b\[48;5;(\d+)m", line)

    ver_bgs = {b for l in lines[:3] for b in _bg(l)}
    hor_bgs = {b for l in lines[3:6] for b in _bg(l)}
    assert ver_bgs and hor_bgs
    assert not (ver_bgs & hor_bgs)


def test_tree_of_a_flat_assembly_is_unchanged():
    asm = _assembly_with([_FakeDetector("hpos", 0.1), _FakeDetector("vpos", 0.2)])

    assert asm.get_display_str(tree=True) == asm.get_display_str(tree=False)


class _FakeParent(_FakeDetector):
    """A sub-assembly ("mother element") that has sub-items *and* a reading
    of its own -- e.g. a device whose value is its main axis."""

    def __init__(self, name, value):
        super().__init__(name, value)
        self.status_collection = object()  # marks it as having sub-items


def test_tree_header_shows_the_parents_own_value_when_it_has_one():
    asm = _nested_assembly()
    asm.ver = _FakeParent("ver", 12.5)

    lines = _plain(asm.get_display_str(tree=True)).splitlines()
    header = next(l for l in lines if l.strip().startswith("ver"))

    assert "12.5" in header
    assert "↳" in header  # still marked as having sub-items
    # the children are unaffected
    assert any("├── x" in l and "0.1" in l for l in lines)


def test_tree_header_stays_a_bare_label_without_an_own_value():
    # 'hor' has no attribute on the assembly at all -> nothing to read
    lines = _plain(_nested_assembly().get_display_str(tree=True)).splitlines()
    header = next(l for l in lines if l.strip().startswith("hor"))

    assert header.split() == ["hor", "↳"]
    # no "has lower level items"/"<no value>" placeholder on a heading row
    assert "has lower level items" not in _plain(
        _nested_assembly().get_display_str(tree=True)
    )


def test_tree_header_value_read_can_never_break_the_table():
    class _Exploding:
        status_collection = object()

        def get_current_value(self):
            raise RuntimeError("channel access timed out")

    asm = _nested_assembly()
    asm.ver = _Exploding()

    lines = _plain(asm.get_display_str(tree=True)).splitlines()

    assert any(l.strip().startswith("ver") for l in lines)
    assert any("├── x" in l and "0.1" in l for l in lines)
