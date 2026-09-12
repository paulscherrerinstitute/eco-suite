"""eco.elements.recent -- FrequencyCounts (the "Recommended" data source)
and touch_from_write's dual bump into both RecentComponents and
FrequencyCounts. RecentComponents itself has no dedicated test file yet;
not backfilled here -- only the new code this session added."""
from eco.elements import recent


def test_frequency_counts_ranks_by_use_count_descending(tmp_path):
    fc = recent.FrequencyCounts(path=tmp_path / "freq.json", debounce_seconds=999)
    for _ in range(3):
        fc.touch("bernina.mono.energy")
    for _ in range(5):
        fc.touch("bernina.attenuator.transmission")
    fc.touch("bernina.slit1.width")

    assert fc.top(2) == ["bernina.attenuator.transmission", "bernina.mono.energy"]
    assert fc.all() == {
        "bernina.mono.energy": 3,
        "bernina.attenuator.transmission": 5,
        "bernina.slit1.width": 1,
    }


def test_frequency_counts_ties_break_alphabetically(tmp_path):
    fc = recent.FrequencyCounts(path=tmp_path / "freq.json", debounce_seconds=999)
    fc.touch("bernina.zzz")
    fc.touch("bernina.aaa")

    assert fc.top() == ["bernina.aaa", "bernina.zzz"]


def test_frequency_counts_touch_ignores_empty_path(tmp_path):
    fc = recent.FrequencyCounts(path=tmp_path / "freq.json", debounce_seconds=999)
    fc.touch("")
    assert fc.all() == {}


def test_frequency_counts_clear(tmp_path):
    fc = recent.FrequencyCounts(path=tmp_path / "freq.json", debounce_seconds=999)
    fc.touch("bernina.mono.energy")
    fc.clear()
    assert fc.all() == {}


def test_frequency_counts_flush_writes_to_disk(tmp_path):
    path = tmp_path / "freq.json"
    fc = recent.FrequencyCounts(path=path, debounce_seconds=999)
    fc.touch("bernina.mono.energy")
    fc.flush()

    fc2 = recent.FrequencyCounts(path=path, debounce_seconds=999)
    assert fc2.all() == {"bernina.mono.energy": 1}


class _FakeComponent:
    def __init__(self, name):
        self.name = name

    @property
    def alias(self):
        raise AttributeError("no real Alias on this fake -- forces the .name fallback")


def test_touch_from_write_bumps_both_recent_and_frequency(monkeypatch, tmp_path):
    monkeypatch.setattr(recent, "_instances", {})
    monkeypatch.setattr(recent, "_frequency_instances", {})
    monkeypatch.setattr(recent, "_current_user", lambda: "test_user")

    recent_inst = recent.RecentComponents(path=tmp_path / "recent.json", debounce_seconds=999)
    freq_inst = recent.FrequencyCounts(path=tmp_path / "freq.json", debounce_seconds=999)
    monkeypatch.setattr(recent, "_instances", {"test_user": recent_inst})
    monkeypatch.setattr(recent, "_frequency_instances", {"test_user": freq_inst})

    recent.touch_from_write(_FakeComponent("bernina.mono.energy"))
    recent.touch_from_write(_FakeComponent("bernina.mono.energy"))

    assert recent_inst.all() == ["bernina.mono.energy"]
    assert freq_inst.all() == {"bernina.mono.energy": 2}


def test_touch_from_write_is_a_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(recent, "enabled", False)
    monkeypatch.setattr(recent, "_instances", {})
    monkeypatch.setattr(recent, "_frequency_instances", {})

    recent.touch_from_write(_FakeComponent("bernina.mono.energy"))

    assert recent._instances == {}
    assert recent._frequency_instances == {}
    monkeypatch.setattr(recent, "enabled", True)
