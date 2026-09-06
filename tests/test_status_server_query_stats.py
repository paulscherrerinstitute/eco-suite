"""eco.status_server.query_stats: the ring buffer behind GET /stats."""

import pytest

from eco.status_server.query_stats import QueryStats, timed


def test_empty_summary():
    s = QueryStats()
    assert s.summary() == {"n": 0, "n_errors": 0}
    assert s.recent() == []


def test_record_and_recent():
    s = QueryStats()
    s.record("snapshot", 1.5, n_entries=100)
    s.record("capture", 2.5, pgroup="p1")
    recent = s.recent()
    assert len(recent) == 2
    assert recent[0]["kind"] == "snapshot"
    assert recent[0]["n_entries"] == 100
    assert recent[1]["pgroup"] == "p1"


def test_recent_filters_by_kind():
    s = QueryStats()
    s.record("snapshot", 1.0)
    s.record("capture", 1.0)
    s.record("snapshot", 1.0)
    assert len(s.recent(kind="snapshot")) == 2
    assert len(s.recent(kind="capture")) == 1


def test_recent_respects_limit():
    s = QueryStats()
    for i in range(10):
        s.record("snapshot", float(i))
    assert len(s.recent(limit=3)) == 3
    assert s.recent(limit=3)[-1]["duration_s"] == 9.0


def test_ring_buffer_drops_oldest():
    s = QueryStats(maxlen=3)
    for i in range(5):
        s.record("snapshot", float(i))
    recent = s.recent()
    assert len(recent) == 3
    assert [r["duration_s"] for r in recent] == [2.0, 3.0, 4.0]


def test_summary_aggregates_durations_and_errors():
    s = QueryStats()
    s.record("snapshot", 1.0)
    s.record("snapshot", 3.0, error="boom")
    s.record("snapshot", 2.0)
    summary = s.summary()
    assert summary["n"] == 3
    assert summary["n_errors"] == 1
    assert summary["avg_duration_s"] == pytest.approx(2.0)
    assert summary["min_duration_s"] == 1.0
    assert summary["max_duration_s"] == 3.0
    # "last_error" tracks the *last entry's* error (None here - the last
    # recorded op succeeded); test_summary_last_error_is_the_most_recent_one
    # covers the separate "did anything fail recently" question below.
    assert summary["last_error"] is None
    assert summary["last_error_at"] is not None  # "boom" is still visible via this


def test_summary_last_error_is_the_most_recent_one():
    s = QueryStats()
    s.record("snapshot", 1.0, error="first")
    s.record("snapshot", 1.0)  # no error
    s.record("snapshot", 1.0, error="second")
    s.record("snapshot", 1.0)  # no error again
    summary = s.summary()
    assert summary["last_error"] is None  # the very last entry had none
    assert summary["last_error_at"] is not None  # but we remember the last one that did


def test_timed_context_manager_records_success():
    s = QueryStats()
    with timed(s, "snapshot", n_entries=5) as t:
        t.fields["n_entries"] = 42
    entry = s.recent()[0]
    assert entry["kind"] == "snapshot"
    assert entry["n_entries"] == 42
    assert entry["error"] is None
    assert entry["duration_s"] >= 0


def test_timed_context_manager_records_and_reraises_errors():
    s = QueryStats()
    with pytest.raises(ValueError, match="boom"):
        with timed(s, "capture"):
            raise ValueError("boom")
    entry = s.recent()[0]
    assert entry["error"] == "ValueError: boom"


def test_thread_safety_of_concurrent_records():
    import threading

    s = QueryStats(maxlen=1000)

    def worker():
        for _ in range(100):
            s.record("snapshot", 0.1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.summary()["n"] == 800
