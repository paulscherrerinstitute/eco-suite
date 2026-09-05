"""eco.utilities.runtable_stripped - the run table actually wired into the
live bernina namespace (`bernina.py`'s ``run_table``/``run_table_old``
components both use ``module_name="eco.utilities.runtable_stripped"``, not
``eco.utilities.runtable``, which is unused dead code kept only for
reference/history - see that module's own docstring).

The module does ``from eco.bernina import namespace`` at import time, which
pulls in the whole bernina beamline definition (~20 s, real config file
reads). Stub a minimal fake at ``sys.modules["eco.bernina"]`` before
importing, so this stays a fast, hermetic unit test - the same reason
``tests/test_daq_status_server.py`` builds a bare ``Daq.__new__(Daq)``
instead of constructing one for real.
"""

import stat
import sys
import threading
import time
import types

import pytest


@pytest.fixture(scope="module")
def rts():
    """The module under test, imported once with eco.bernina stubbed out."""
    if "eco.bernina" not in sys.modules:
        fake_pkg = types.ModuleType("eco.bernina")
        fake_pkg.namespace = types.SimpleNamespace(
            config_bernina=types.SimpleNamespace(
                pgroup=types.SimpleNamespace(_value="p00000")
            )
        )
        sys.modules["eco.bernina"] = fake_pkg
        installed_fake = True
    else:
        installed_fake = False

    import eco.utilities.runtable_stripped as rts

    yield rts

    if installed_fake:
        del sys.modules["eco.bernina"]


def _table(rts, tmp_path, **kwargs):
    """A Run_Table2 with no Google Sheets integration and parse=False, i.e.
    exactly the configuration bernina.py constructs it with."""
    kwargs.setdefault("exp_id", "p00000")
    kwargs.setdefault("exp_path", str(tmp_path) + "/")
    kwargs.setdefault("devices", None)
    kwargs.setdefault("name", "test")
    kwargs.setdefault("parse", False)
    return rts.Run_Table2(**kwargs)


# --------------------------------------------------------------------------
# save(): the permission fix


def test_save_creates_a_group_writable_setgid_tree(rts, tmp_path):
    """save() used to be mkdir(parents=True) + chmod(0o775) on the leaf only
    - the CLAUDE.md-documented trap: intermediate levels land at the
    umask-masked 0o755, and a plain chmod clears setgid if it was ever set."""
    nested = tmp_path / "run_data" / "run_table"
    table = _table(rts, tmp_path, exp_path=str(nested) + "/")
    table._data.append_run(1, metadata={"type": "test"}, d={"bernina.a": 1})

    pkl = nested / "p00000_runtable.pkl"
    assert pkl.exists()
    assert stat.S_IMODE(pkl.stat().st_mode) & 0o664 == 0o664
    for level in (nested, nested.parent):
        mode = level.stat().st_mode
        assert stat.S_IMODE(mode) & 0o770 == 0o770
        assert mode & stat.S_ISGID, f"{level} lost its setgid bit"


def test_save_round_trips_through_a_second_append(rts, tmp_path):
    table = _table(rts, tmp_path)
    table._data.append_run(1, metadata={"type": "test"}, d={"bernina.a": 1})
    table._data.append_run(2, metadata={"type": "test"}, d={"bernina.a": 2})
    table._data.load()
    assert list(table._data.df.index) == [1, 2]


# --------------------------------------------------------------------------
# Run_Table2.append_run/append_pos: the concurrent-write race


def test_overlapping_appends_are_serialized_by_the_lock(rts, tmp_path):
    """append_run/append_pos each fire on their own thread and every one of
    them does load -> concat -> save on the same pickle; two overlapping
    ones used to lose a row (the second one's load() sees the frame from
    before the first one's save()). Daq now defers the run-table append
    until the status server has the values, which widens that window.

    Proven directly, not just by outcome: record entry/exit around the
    critical section from inside a slowed-down load() and assert no two
    overlap - the more direct statement of "the lock works" than hoping a
    timing-dependent interleaving would otherwise have lost a row."""
    table = _table(rts, tmp_path)
    real_load = table._data.load
    intervals = []
    lock = threading.Lock()

    def slow_load():
        t0 = time.monotonic()
        real_load()
        time.sleep(0.05)
        with lock:
            intervals.append((t0, time.monotonic()))

    table._data.load = slow_load
    threads = [
        threading.Thread(
            target=table.append_run,
            args=(n,),
            kwargs={"metadata": {"type": "test"}, "d": {"bernina.a": n}, "wait": True},
        )
        for n in (1, 2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    table._data.load = real_load

    assert len(intervals) == 2
    (s1, e1), (s2, e2) = sorted(intervals)
    assert e1 <= s2, f"critical sections overlapped: {intervals}"

    table._data.load()
    assert sorted(table._data.df.index) == [1, 2]


def test_append_run_and_append_pos_share_the_same_lock(rts, tmp_path):
    table = _table(rts, tmp_path)
    assert table._append_lock is not None
    # both call sites take it - see the source, this just guards against a
    # future edit dropping one of the two `with self._append_lock:` blocks
    import inspect

    src_run = inspect.getsource(table._append_run)
    src_pos = inspect.getsource(table._append_pos)
    assert "_append_lock" in src_run
    assert "_append_lock" in src_pos
