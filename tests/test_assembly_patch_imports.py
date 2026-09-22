"""`Assembly._append(..., add_patch=True)` must write a patch that still runs
in a fresh session: imports for what the captured cell uses (see
`eco.elements.assembly._patch_prelude`)."""

import collections
import types

from eco.elements import assembly as asm_mod
from eco.elements.assembly import Assembly, _capture_current_input, _patch_prelude


def test_class_used_in_code_gets_from_import():
    imports, cells, notes = _patch_prelude(
        "a._append(OrderedDict, name='x')",
        {"OrderedDict": collections.OrderedDict, "a": object()},
        known_names={"a"},
    )
    assert imports == ["from collections import OrderedDict"]
    assert cells == [] and notes == []


def test_alias_and_module_imports():
    ns = {"np": types.ModuleType("numpy"), "OD": collections.OrderedDict}
    imports, _, _ = _patch_prelude("np.x; OD()", ns)
    assert sorted(imports) == ["from collections import OrderedDict as OD", "import numpy as np"]


def test_names_from_root_module_builtins_and_local_bindings_are_skipped():
    ns = {"mono": 1, "OrderedDict": collections.OrderedDict, "helper": len}
    code = "helper = 3\nlen([mono, OrderedDict])\nfor i in range(2): pass"
    assert _patch_prelude(code, ns, known_names={"OrderedDict", "mono"}) == ([], [], [])


def test_session_only_variable_is_quoted_when_replay_is_off():
    ns = {"x": 5}
    history = ["", "x = move_motor(3)\n", "a._append(x, name='n')"]
    imports, cells, notes = _patch_prelude(history[-1], ns, history, replay=False)
    assert imports == [] and cells == []
    assert "'x' is not importable" in notes[0]
    assert "#     x = move_motor(3)" in notes
    assert all(n.startswith("#") for n in notes)  # quoted, never executable


def test_origin_cells_are_replayed_recursively_in_order():
    ns = {"OrderedDict": collections.OrderedDict, "pv": 1, "adj": 2, "unrelated": 3}
    history = [
        "",
        "pv = 'SARES:X'",
        "unrelated = 3",
        "adj = OrderedDict(name=pv)",
        "print(1)",
        "a._append(adj, name='n')",
    ]
    imports, cells, notes = _patch_prelude(history[-1], ns, history, known_names={"a"})
    assert imports == ["from collections import OrderedDict"]
    assert cells == [(1, "pv = 'SARES:X'"), (3, "adj = OrderedDict(name=pv)")]
    assert notes == []


def test_replay_follows_earlier_definitions_of_a_reassigned_name():
    ns = {"n": 3}
    history = ["", "n = 1", "n = 5", "n += 1", "a._append(n)"]
    _, cells, _ = _patch_prelude(history[-1], ns, history, known_names={"a"})
    assert [i for i, _ in cells] == [2, 3]  # `n += 1` needs n = 5, not n = 1


def test_class_defined_in_main_is_not_imported():
    class Local:
        pass

    Local.__module__ = "__main__"
    imports, _, notes = _patch_prelude("Local()", {"Local": Local})
    assert imports == [] and notes  # nothing to replay from: reported


def test_unparseable_code_is_ignored():
    assert _patch_prelude("this is not python", {}) == ([], [], [])


def _session(tmp_path):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = TerminalInteractiveShell.instance()
    top = Assembly(name="top")
    top.patch_file = tmp_path / "patches.py"
    ip.user_ns.update(top=top, Assembly=Assembly)
    return ip, top


def test_end_to_end_imports_and_replayed_origin(tmp_path):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip, top = _session(tmp_path)
    try:
        ip.run_cell("label = 'hello'", store_history=True)
        ip.run_cell("base = %pwd", store_history=True)
        ip.run_cell("other = 1", store_history=True)
        ip.run_cell(
            "top._append(Assembly, name=label + str(bool(base)), add_patch=True)",
            store_history=True,
        )
        assert "helloTrue" in top.__dict__
        text = (tmp_path / "patches.py").read_text()
        assert text.index("from eco.elements.assembly import Assembly") < text.index("label = 'hello'")
        assert text.index("label = 'hello'") < text.index("top._append")
        assert "other = 1" not in text
        # the patch is self-contained: replay it against a fresh assembly
        fresh = Assembly(name="top")
        exec(compile(text, "patches.py", "exec"), {"top": fresh})
        assert "helloTrue" in fresh.__dict__
    finally:
        TerminalInteractiveShell.clear_instance()


def test_end_to_end_replay_can_be_switched_off(tmp_path):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip, top = _session(tmp_path)
    try:
        ip.run_cell("label = 'hello'", store_history=True)
        ip.run_cell(
            "top._append(Assembly, name=label, add_patch=True, patch_replay_origin=False)",
            store_history=True,
        )
        lines = (tmp_path / "patches.py").read_text().splitlines()
        assert "label = 'hello'" not in lines  # only quoted, as a comment
        assert "#     label = 'hello'" in lines
    finally:
        TerminalInteractiveShell.clear_instance()
