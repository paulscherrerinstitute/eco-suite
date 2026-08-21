from eco.widgets.log_timeline_common import TimelineEntry, bucket_density, nearest_entry


def _entry(t, is_error=False):
    return TimelineEntry(t=t, kind="input", text=f"t={t}", is_error=is_error)


def test_timeline_entry_defaults():
    e = TimelineEntry(t=1.0, kind="input", text="x")
    assert e.tags == ()
    assert e.ref is None
    assert e.is_error is False


def test_bucket_density_counts_land_in_expected_bucket():
    entries = [_entry(0.0), _entry(0.0), _entry(50.0), _entry(99.9)]
    counts, errors = bucket_density(entries, 0.0, 100.0, n_buckets=10)
    assert len(counts) == 10
    assert counts[0] == 2  # both t=0.0 entries
    assert counts[5] == 1  # t=50.0 -> bucket 5
    assert counts[9] == 1  # t=99.9 -> last bucket
    assert sum(counts) == 4


def test_bucket_density_ignores_entries_outside_window():
    entries = [_entry(-10.0), _entry(50.0), _entry(200.0)]
    counts, _ = bucket_density(entries, 0.0, 100.0, n_buckets=10)
    assert sum(counts) == 1


def test_bucket_density_flags_error_buckets():
    entries = [_entry(10.0), _entry(15.0, is_error=True)]
    counts, errors = bucket_density(entries, 0.0, 100.0, n_buckets=10)
    assert counts[1] == 2
    assert errors[1] is True
    assert all(not e for i, e in enumerate(errors) if i != 1)


def test_bucket_density_empty_entries():
    counts, errors = bucket_density([], 0.0, 100.0, n_buckets=10)
    assert counts == [0] * 10
    assert errors == [False] * 10


def test_nearest_entry_picks_closer_neighbor():
    entries = [_entry(0.0), _entry(10.0), _entry(20.0)]
    assert nearest_entry(entries, 4.0) is entries[0]
    assert nearest_entry(entries, 6.0) is entries[1]
    assert nearest_entry(entries, 20.0) is entries[2]


def test_nearest_entry_clamps_outside_range():
    entries = [_entry(10.0), _entry(20.0)]
    assert nearest_entry(entries, -100.0) is entries[0]
    assert nearest_entry(entries, 100.0) is entries[1]


def test_nearest_entry_empty_list_returns_none():
    assert nearest_entry([], 5.0) is None
