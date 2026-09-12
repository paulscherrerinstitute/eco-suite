"""eco.logs.recent_commands / frequent_commands -- the command-history
data sources for eco.ipymagic's command-picker mode. Real KernelSession
files (not hand-written JSONL) so this also incidentally exercises the
real on-disk format recent_commands/frequent_commands actually parse."""
import pytest

from eco.widgets import kernel_registry


@pytest.fixture(autouse=True)
def _isolated_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kernel_registry, "DEFAULT_LOG_DIR", tmp_path)
    monkeypatch.setattr(kernel_registry, "_registry", [])
    return tmp_path


def _session(tmp_path, kind="console", label="bernina"):
    return kernel_registry.register(
        kernel_registry.KernelSession(kind=kind, label=label, log_dir=tmp_path)
    )


def test_recent_commands_are_most_recent_first_and_deduplicated(tmp_path):
    from eco import logs

    session = _session(tmp_path)
    session.log_input("daq.compare_channels()")
    session.log_input("mono.mv(5)")
    session.log_input("daq.compare_channels()")  # repeat -- should move to front, not duplicate

    assert logs.recent_commands() == ["daq.compare_channels()", "mono.mv(5)"]


def test_recent_commands_ignores_blank_input(tmp_path):
    from eco import logs

    session = _session(tmp_path)
    session.log_input("   ")
    session.log_input("mono.mv(5)")

    assert logs.recent_commands() == ["mono.mv(5)"]


def test_recent_commands_merges_across_sessions(tmp_path):
    from eco import logs

    s1 = _session(tmp_path, label="bernina")
    s1.log_input("mono.mv(5)")
    s2 = _session(tmp_path, label="bernina2")
    s2.log_input("daq.compare_channels()")

    assert set(logs.recent_commands()) == {"mono.mv(5)", "daq.compare_channels()"}


def test_recent_commands_respects_n(tmp_path):
    from eco import logs

    session = _session(tmp_path)
    for i in range(5):
        session.log_input(f"cmd_{i}()")

    assert len(logs.recent_commands(n=2)) == 2


def test_frequent_commands_ranks_by_use_count(tmp_path):
    from eco import logs

    session = _session(tmp_path)
    for _ in range(3):
        session.log_input("daq.compare_channels()")
    session.log_input("mono.mv(5)")

    assert logs.frequent_commands()[:2] == ["daq.compare_channels()", "mono.mv(5)"]


def test_frequent_commands_ties_break_by_recency(tmp_path):
    import time

    from eco import logs

    session = _session(tmp_path)
    session.log_input("older_but_tied()")
    time.sleep(0.01)  # guarantee a distinguishable timestamp, not just a distinguishable call
    session.log_input("newer_and_tied()")

    assert logs.frequent_commands()[:2] == ["newer_and_tied()", "older_but_tied()"]
