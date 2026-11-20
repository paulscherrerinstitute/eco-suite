"""`runs.add_append_callback`: derived items every loaded run gets, kept in the
pgroup's run patch file (eco.acquisition.scan_data.RunData, escape's
DataSet.run_append_callbacks)."""

import sys
import types

import pytest

escape = pytest.importorskip("escape")
if not hasattr(escape.DataSet, "run_append_callbacks"):
    pytest.skip("escape without DataSet append callbacks", allow_module_level=True)

from eco.acquisition.scan_data import RunData


class _Pgroup:
    def get_current_value(self):
        return "p00000"


def _dataset(a=6.0, b=3.0):
    ds = escape.DataSet()
    ds.append(a, name="a")
    ds.append(b, name="b")
    return ds


@pytest.fixture
def runs(tmp_path, monkeypatch):
    loaded = {}

    def load_dataset_from_scan(pgroup, run_numbers, **kwargs):
        loaded[run_numbers[0]] = ds = _dataset(a=float(run_numbers[0]))
        return ds

    fake = types.ModuleType("escape.swissfel")
    fake.load_dataset_from_scan = load_dataset_from_scan
    monkeypatch.setitem(sys.modules, "escape.swissfel", fake)
    return RunData(_Pgroup(), patch_path=str(tmp_path / "{pgroup}" / "run_patches.py"))


def test_dataset_runs_callbacks_in_order_and_skips_failures():
    ds = _dataset()
    ds.add_append_callback("ratio", lambda d: d.a / d.b)
    ds.add_append_callback("broken", lambda d: d.missing)
    ds.add_append_callback("double", lambda d: 2 * d.ratio)  # uses an earlier one
    applied, failed = ds.run_append_callbacks()
    assert applied == ["ratio", "double"] and failed == ["broken"]
    assert ds.double == 4.0
    ds.add_append_callback("ratio", lambda d: d.b / d.a)  # replaced in place
    assert [n for n, _ in ds.append_callbacks] == ["ratio", "broken", "double"]
    assert "append_callbacks" not in repr(ds)


def test_loaded_and_later_runs_get_the_callback(runs):
    first = runs[1]
    runs.add_append_callback("ratio", lambda d: d.a / d.b)
    assert first.ratio == pytest.approx(1 / 3)  # applied to the loaded run
    assert runs[6].ratio == 2.0  # and to the next one


def test_patch_is_recorded_and_restores_the_callback(runs, tmp_path):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = TerminalInteractiveShell.instance()
    try:
        ip.user_ns["runs"] = runs
        ip.run_cell("scale = 10", store_history=True)
        ip.run_cell("def norm(d):\n    return scale * d.a / d.b", store_history=True)
        ip.run_cell("runs.add_append_callback('norm', norm, add_patch=True)", store_history=True)
        ip.run_cell("ds = runs[3]", store_history=True)  # replay must not record this
        assert ip.user_ns["ds"].norm == 10.0
        path = tmp_path / "p00000" / "run_patches.py"
        text = path.read_text()
        assert text.count("# --- patch for runs.append_callbacks.norm") == 1
        assert text.index("scale = 10") < text.index("def norm") < text.index(
            "runs.add_append_callback('norm', norm)"
        )
        assert "runs[3]" not in text
    finally:
        TerminalInteractiveShell.clear_instance()

    # a fresh session: the patch file alone brings the callback back
    fresh = RunData(_Pgroup(), patch_path=runs.patch_path)
    fresh.loaded_runs.clear()
    assert [n for n, _ in fresh.append_callbacks] == ["norm"]
    ds = _dataset(a=9.0)
    fresh.apply_append_callbacks(ds)
    assert ds.norm == 30.0


def test_recorded_removal_is_replayed(runs, tmp_path):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = TerminalInteractiveShell.instance()
    try:
        ip.user_ns["runs"] = runs
        ip.run_cell(
            "runs.add_append_callback('s', lambda d: d.a + d.b, add_patch=True)",
            store_history=True,
        )
        ip.run_cell("runs.remove_append_callback('s', add_patch=True)", store_history=True)
    finally:
        TerminalInteractiveShell.clear_instance()
    fresh = RunData(_Pgroup(), patch_path=runs.patch_path)
    assert fresh.append_callbacks == []


def test_change_by_another_session_is_picked_up(runs, tmp_path):
    path = tmp_path / "p00000" / "run_patches.py"
    path.parent.mkdir()
    path.write_text(
        "# --- patch for runs.append_callbacks.x (2026-01-01T00:00:00, u) ---\n"
        "runs.add_append_callback('x', lambda d: 1)\n"
    )
    assert [n for n, _ in runs.append_callbacks] == ["x"]
    import os, time

    with open(path, "a") as f:
        f.write(
            "# --- patch for runs.append_callbacks.y (2026-01-01T00:00:01, u) ---\n"
            "runs.add_append_callback('y', lambda d: 2)\n"
        )
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert runs[2].y == 2


def _fresh_load_counter(monkeypatch):
    calls = []

    def load_dataset_from_scan(pgroup, run_numbers, **kwargs):
        calls.append(run_numbers[0])
        return _dataset(a=float(run_numbers[0]))

    fake = types.ModuleType("escape.swissfel")
    fake.load_dataset_from_scan = load_dataset_from_scan
    monkeypatch.setitem(sys.modules, "escape.swissfel", fake)
    return calls


def _shell(runs):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = TerminalInteractiveShell.instance()
    ip.user_ns["runs"] = runs
    return ip


def _run(ip, cell):
    result = ip.run_cell(cell, store_history=True)
    result.raise_error()
    return result


def test_run_patch_from_an_object_computed_on_an_example_run(runs, tmp_path, monkeypatch):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = _shell(runs)
    try:
        _run(ip, "import math")
        _run(ip, "scale = 4")
        _run(ip, "d = runs[3]\nprint('looking at', d)")
        _run(ip, "unrelated = d.b * 100")
        _run(ip, "def helper(x):\n    return math.sqrt(x)")
        _run(ip, "i0 = helper(d.a * scale) / d.b\nprint(i0)")
        _run(ip, "runs.append_run_patch(i0, name='i0')")
        assert ip.user_ns["d"].i0 == pytest.approx(math_sqrt(12) / 3)  # applied to d
        text = (tmp_path / "p00000" / "run_patches.py").read_text()
        body = text[text.index("def _run_patch_i0(dataset):"):]
        assert "    import math\n" in body
        assert "    d = dataset\n" in body
        assert body.index("scale = 4") < body.index("def helper") < body.index("i0 = helper")
        assert "runs[3]" not in text and "unrelated" not in text and "print" not in text
        assert "runs.add_append_callback('i0', _run_patch_i0)" in text
    finally:
        TerminalInteractiveShell.clear_instance()
    calls = _fresh_load_counter(monkeypatch)
    fresh = RunData(_Pgroup(), patch_path=runs.patch_path)
    assert fresh[12].i0 == pytest.approx(math_sqrt(48) / 3)
    assert calls == [12]  # the example run is never loaded again


def math_sqrt(x):
    import math

    return math.sqrt(x)


def test_run_patch_from_an_inline_expression_and_a_reassigned_name(runs, tmp_path):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = _shell(runs)
    try:
        _run(ip, "ex = runs[5]\nk = 1\nk += 1")
        _run(ip, "runs.append_run_patch(ex.a * k, name='a2')")
        assert ip.user_ns["ex"].a2 == 10.0
        assert runs[7].a2 == 14.0
        text = (tmp_path / "p00000" / "run_patches.py").read_text()
        assert "k = 1\n    k += 1\n    return ex.a * k" in text
    finally:
        TerminalInteractiveShell.clear_instance()


def test_run_patch_refuses_what_it_cannot_reconstruct(runs):
    from IPython.terminal.interactiveshell import TerminalInteractiveShell

    ip = _shell(runs)
    try:
        _run(ip, "c = 3")
        with pytest.raises(ValueError, match="does not depend on a loaded run"):
            _run(ip, "runs.append_run_patch(c * 2, name='x')")
        ip.user_ns["ghost"] = 5  # exists, but no statement made it
        _run(ip, "d = runs[1]")
        with pytest.raises(ValueError, match="no statement in the session binds ghost"):
            _run(ip, "runs.append_run_patch(d.a * ghost, name='y')")
    finally:
        TerminalInteractiveShell.clear_instance()
