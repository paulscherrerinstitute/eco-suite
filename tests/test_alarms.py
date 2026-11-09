"""Alarm system: tracker timing, settings store, shift plan, monitor paging."""

from datetime import datetime, timedelta

import pytest

from eco.alarms import notify as nt
from eco.alarms import tracker as tr


@pytest.fixture(autouse=True)
def alarm_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ECO_ALARM_DIR", str(tmp_path / "alarms"))
    from eco.alarms import store

    for f in (store.SETTINGS, store.CUSTOM, store.SYSTEM, store.PEOPLE, store.STATE, store.SHIFT_CACHE):
        f._cache = None
    yield tmp_path / "alarms"


# ---------------------------------------------------------------- tracker


def test_tracker_delay_then_raise_and_clear_delay():
    t = tr.AlarmTracker()
    assert t.update(tr.BAD, 0, delay=10) == []
    assert t.state == tr.PENDING
    assert t.update(tr.BAD, 9.9, delay=10) == []
    assert t.update(tr.BAD, 10, delay=10) == ["raised"]
    assert t.state == tr.ACTIVE and t.count == 1
    # brief recovery shorter than clear_delay keeps it raised
    assert t.update(tr.OK, 12, clear_delay=5) == []
    assert t.state == tr.CLEARING
    assert t.update(tr.BAD, 13, clear_delay=5) == []
    assert t.state == tr.ACTIVE
    t.update(tr.OK, 20, clear_delay=5)
    assert t.update(tr.OK, 25, clear_delay=5) == ["cleared"]
    assert t.state == tr.STATE_OK
    assert t.time_in_alarm(100) == pytest.approx(15)


def test_tracker_pending_resets_on_recovery():
    t = tr.AlarmTracker()
    t.update(tr.BAD, 0, delay=10)
    t.update(tr.OK, 5, delay=10)
    t.update(tr.BAD, 6, delay=10)
    assert t.update(tr.BAD, 15, delay=10) == []  # only 9 s since 6
    assert t.update(tr.BAD, 16, delay=10) == ["raised"]


def test_tracker_ack_and_disable_clears():
    t = tr.AlarmTracker()
    t.update(tr.BAD, 0)
    assert t.ack(1, by="me") == ["acked"]
    assert t.state == tr.ACKED and t.acked_by == "me"
    assert t.ack(2) == []
    assert t.update(tr.BAD, 3, enabled=False) == ["cleared"]
    assert t.state == tr.DISABLED and not t.acked


def test_tracker_unknown_and_inhibit_and_shelve():
    t = tr.AlarmTracker()
    assert t.update(tr.UNKNOWN, 0) == []
    assert t.state == tr.STATE_UNKNOWN
    assert t.update(tr.UNKNOWN, 1, unknown_is_alarm=True) == ["raised"]
    assert t.update(tr.BAD, 2, inhibited=True) == ["cleared"]
    assert t.state == tr.INHIBITED
    assert t.update(tr.BAD, 3, shelved_until=10) == []
    assert t.state == tr.SHELVED
    assert t.update(tr.BAD, 11, shelved_until=10) == ["raised"]


def test_tracker_roundtrip():
    t = tr.AlarmTracker()
    t.update(tr.BAD, 0)
    t2 = tr.AlarmTracker.from_dict(t.to_dict())
    assert t2.state == tr.ACTIVE and t2.count == 1


# ---------------------------------------------------------------- Alarm


def _plain(s):
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", s)


class Box:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _alarm(box, **kw):
    from eco.elements.alarm import Alarm
    from eco.elements.detector import DetectorGet

    return Alarm(DetectorGet(box.get, name="det"), name="a1", **kw)


def test_alarm_settings_are_shared_overrides(alarm_dir):
    from eco.alarms import store

    box = Box(5.0)
    a = _alarm(box, min=0, max=10, delay=0)
    assert a.get_condition()[0] == tr.OK
    a.max.set_target_value(4).wait()
    assert a.get_setting("max") == 4
    assert a.get_condition()[0] == tr.BAD
    # stored in the shared file, under the alarm id, only the override
    store.SETTINGS._cache = None
    assert store.SETTINGS.read()["a1"]["max"] == 4
    assert (alarm_dir / "settings.json").exists()
    # a second object with the same id sees it
    b = _alarm(Box(5.0), min=0, max=10)
    store.SETTINGS._cache = None
    assert b.get_setting("max") == 4
    a.reset_settings()
    store.SETTINGS._cache = None
    assert b.get_setting("max") == 10
    with pytest.raises(ValueError):
        a.set_setting("level", "loud")


def test_alarm_check_ack_request_and_display():
    box = Box(5.0)
    a = _alarm(box, min=0, max=10, delay=0, clear_delay=0, unit="mJ")
    assert a.check() == []
    box.v = 11
    assert a.check() == ["raised"]
    a.ack(by="tester")
    assert a.tracker.state == tr.ACKED
    s = _plain(a.get_display_str())
    assert "ACKED · tester" in s and "11 mJ" in s and "[0, 10]" in s
    assert a.get_severity().name == "ALARM"


def test_panel_display_and_svg(tmp_path):
    from eco.elements.alarm import AlarmPanel
    from eco.elements.detector import DetectorGet

    box = Box(3.0)
    panel = AlarmPanel(
        [("Sec A", [{"name": "x", "detector": DetectorGet(box.get), "min": 0, "max": 1, "delay": 0}]),
         ("Sec B", [{"name": "y", "detector": DetectorGet(lambda: None), "min": 0, "max": 1}])],
        name="p",
    )
    s = _plain(panel.get_display_str())
    assert "Sec A.x" in s and "ACTIVE" in s and "unknown" in s
    path = panel._widget_svg_panel(live=True, path=str(tmp_path / "p.svg"))
    svg = open(path).read()
    assert "// eco: sec_a.x" in svg and "ACTIVE" in svg


def test_inhibit_by_with_partial_id():
    from eco.elements.alarm import AlarmPanel, evaluate_alarms
    from eco.elements.detector import DetectorGet

    beam, x = Box(0.0), Box(9.0)
    panel = AlarmPanel(
        [("S", [{"name": "beam", "detector": DetectorGet(beam.get), "min": 1, "delay": 0},
                {"name": "x", "detector": DetectorGet(x.get), "max": 1, "delay": 0,
                 "inhibit_by": ["s.beam"]}])],
        name="bernina_like",
    )
    a_beam, a_x = panel.get_alarms()
    evaluate_alarms([a_x, a_beam], now=1)
    assert a_beam.tracker.state == tr.ACTIVE and a_x.tracker.state == tr.INHIBITED
    beam.v = 5
    # the beam alarm is still clearing (clear_delay 10 s): x stays inhibited,
    # which gives things time to settle after the beam is back
    evaluate_alarms([a_x, a_beam], now=2)
    assert a_beam.tracker.state == tr.CLEARING and a_x.tracker.state == tr.INHIBITED
    evaluate_alarms([a_x, a_beam], now=13)
    assert a_beam.tracker.state == tr.STATE_OK and a_x.tracker.state == tr.ACTIVE
    # a shelved or disabled beam alarm still inhibits while there is no beam,
    # and stops inhibiting once there is
    a_beam.shelve(60)
    beam.v = 0
    evaluate_alarms([a_x, a_beam], now=14)
    assert a_beam.tracker.state == tr.SHELVED and a_x.tracker.state == tr.INHIBITED
    beam.v = 5
    evaluate_alarms([a_x, a_beam], now=15)
    assert a_x.tracker.state != tr.INHIBITED


# ---------------------------------------------------------------- shifts


def _tables():
    return {
        "Shifts": [
            ["Date", "From", "To", "On call", "Backup", "Note"],
            ["2026-11-12", "07:00", "15:00", "Anna, Ben", "Henrik", ""],
            ["2026-11-12", "15:00", "23:00", "Chris", "Henrik", ""],
            ["12.11.2026", "23:00", "07:00", "Dana", "Ben", "night"],
        ],
        "Deviations": [
            ["Person", "From", "To", "Type", "Note"],
            ["ben", "2026-11-12 10:00", "2026-11-12 12:00", "off", "dentist"],
            ["Henrik", "2026-11-13 00:00", "2026-11-13 06:00", "on", ""],
            ["Eve", "2026-11-13", "", "backup", "whole day"],
            ["Bad", "tomorrow", "", "off", ""],
        ],
        "People": [
            ["Name", "Off-shift alerts", "Escalation", "Note"],
            ["Henrik", "quiet", "yes", ""],
            ["Ben", "normal", "no", ""],
        ],
    }


def _t(s):
    from eco.alarms.shifts import TZ

    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def test_shift_plan_resolution():
    from eco.alarms.shifts import ShiftPlan

    plan = ShiftPlan.from_tables(_tables())
    assert any("Deviations row 5" in p for p in plan.problems)
    oc = plan.on_call(_t("2026-11-12 08:00"))
    assert oc.primary == ["Anna", "Ben"] and oc.backup == ["Henrik"]
    oc = plan.on_call(_t("2026-11-12 11:00"))
    assert oc.primary == ["Anna"] and oc.off == ["Ben"]  # deviation, case-insensitive
    oc = plan.on_call(_t("2026-11-13 02:00"))  # night shift into next day
    assert oc.primary == ["Dana", "Henrik"]
    assert oc.backup == ["Ben", "Eve"]
    assert plan.on_call(_t("2026-11-12 08:00")).next_change == _t("2026-11-12 10:00")
    assert plan.on_call(_t("2026-11-14 12:00")).primary == []
    assert plan.escalation_contacts() == ["Henrik"]
    tl = plan.timeline(_t("2026-11-12 06:00"), hours=30)
    assert [oc.primary for _, _, oc in tl][:4] == [[], ["Anna", "Ben"], ["Anna"], ["Anna", "Ben"]]


def test_shift_overrides_from_eco():
    from eco.alarms.shifts import ShiftPlan

    t0 = _t("2026-11-12 16:00").timestamp()
    ov = [{"person": "Henrik", "from": t0, "to": t0 + 3600, "type": "on"}]
    plan = ShiftPlan.from_tables(_tables(), ov)
    oc = plan.on_call(_t("2026-11-12 16:30"))
    assert oc.primary == ["Chris", "Henrik"] and oc.backup == []


# ---------------------------------------------------------------- monitor


@pytest.fixture
def setup_monitor():
    from eco.alarms import store
    from eco.alarms.monitor import AlarmMonitor
    from eco.alarms.shifts import ShiftPlan
    from eco.alarms.system import AlarmSystem
    from eco.elements.alarm import AlarmPanel
    from eco.elements.detector import DetectorGet

    box = Box(0.5)
    panel = AlarmPanel(
        [("Beam", [{"name": "intensity", "detector": DetectorGet(box.get), "min": 0, "max": 1,
                    "delay": 0, "clear_delay": 0, "level": "critical"}])],
        name="ov",
    )
    system = AlarmSystem(panels={"ov": panel}, name="alarms", namespace=object())
    store.PEOPLE.write({n: {"pushover": f"key-{n}"} for n in ("Anna", "Ben", "Henrik", "Chris", "Dana")})
    store.SYSTEM.write({"armed": True, "escalate_after": 600, "receipt_poll_period": 0})
    backend = nt.DryRunBackend()
    plan = ShiftPlan.from_tables(_tables())
    mon = AlarmMonitor(system, backend=backend, plan_loader=lambda now: plan)
    return box, system, mon, backend


def _sent(backend):
    return [(s["person"], s["msg"].priority) for s in backend.sent if "person" in s]


def test_monitor_pages_escalates_and_acks(setup_monitor):
    box, system, mon, backend = setup_monitor
    t = _t("2026-11-12 08:00").timestamp()
    mon.step(now=t)
    assert backend.sent == []
    box.v = 3.0
    mon.step(now=t + 2)
    sent = _sent(backend)
    # on call get emergency; off-shift Ben is on call here, Henrik (quiet pref) gets quiet
    assert ("Anna", nt.EMERGENCY) in sent and ("Ben", nt.EMERGENCY) in sent
    assert ("Henrik", nt.QUIET) in sent
    # not acked -> backup escalated after 600 s
    backend.sent.clear()
    mon.step(now=t + 400)
    assert _sent(backend) == []
    mon.step(now=t + 610)
    assert ("Henrik", nt.EMERGENCY) in _sent(backend)
    # Anna acknowledges on her phone
    page = mon._pages["alarms.ov.beam.intensity"]
    backend.fake_ack(page["receipts"]["Anna"], "key-Anna")
    backend.sent.clear()
    mon.step(now=t + 620)
    a = system.find("intensity")
    assert a.tracker.state == tr.ACKED and a.tracker.acked_by == "Anna (phone)"
    assert any("cancel" in s for s in backend.sent)
    assert ("Ben", nt.QUIET) in _sent(backend)  # told it was acknowledged
    # clears -> cleared message to those notified
    box.v = 0.5
    backend.sent.clear()
    mon.step(now=t + 700)
    assert a.tracker.state == tr.STATE_OK
    assert ("Ben", nt.QUIET) in _sent(backend)
    # state file for displays
    from eco.alarms import store

    doc = store.STATE.read(max_age=0)
    assert doc["monitor"]["armed"] and doc["oncall"]["primary"] == ["Anna", "Ben"]
    assert doc["alarms"]["alarms.ov.beam.intensity"]["count"] == 1


def test_monitor_disarmed_sends_nothing(setup_monitor):
    from eco.alarms import store

    box, system, mon, backend = setup_monitor
    store.SYSTEM.update(lambda d: d.update(armed=False))
    box.v = 3.0
    mon.step(now=_t("2026-11-12 08:00").timestamp())
    assert backend.sent == []
    assert system.find("intensity").tracker.state == tr.ACTIVE


def test_monitor_display_level_and_flapping(setup_monitor):
    box, system, mon, backend = setup_monitor
    a = system.find("intensity")
    a.level.set_target_value("display").wait()
    t = _t("2026-11-12 08:00").timestamp()
    box.v = 3.0
    mon.step(now=t)
    assert backend.sent == []
    a.level.set_target_value("critical").wait()
    for i in range(1, 10):
        box.v = 0.5
        mon.step(now=t + 10 * i)
        box.v = 3.0
        mon.step(now=t + 10 * i + 5)
    # 6 raises paged, then one "flapping" notice, then silence
    titles = [s["msg"].title for s in backend.sent if s.get("person") == "Anna"]
    assert sum("keeps flapping" in x for x in titles) == 1
    assert sum(x.startswith("Bernina ALARM") for x in titles) == 6


def test_monitor_nobody_on_call_goes_to_escalation(setup_monitor):
    box, system, mon, backend = setup_monitor
    box.v = 3.0
    mon.step(now=_t("2026-11-14 12:00").timestamp())
    assert _sent(backend) == [("Henrik", nt.EMERGENCY), ("Ben", nt.NORMAL)]


def test_system_display(setup_monitor):
    box, system, mon, backend = setup_monitor
    box.v = 3.0
    mon.step(now=_t("2026-11-12 08:00").timestamp())
    s = _plain(system.get_display_str())
    assert "ARMED" in s and "intensity" in s


# ---------------------------------------------------------------- Qt


def test_qt_alarm_windows(setup_monitor):
    pytest.importorskip("qtpy")
    import time as _time

    from qtpy import QtWidgets

    from eco.widgets.alarms_qt import AlarmDetailQt, AlarmTableQt

    box, system, mon, backend = setup_monitor
    box.v = 3.0
    mon.step()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    table = AlarmTableQt(system).start()
    detail = AlarmDetailQt(system.find("intensity")).start()
    t_end = _time.time() + 3
    while _time.time() < t_end and table._table.rowCount() < 2:
        app.processEvents()
        _time.sleep(0.02)
    # one section line + one alarm line
    assert table._table.rowCount() == 2
    assert table._lines[1][1]["state"] == "active"
    assert table._arm_btn.text() == "Disarm…"
    t_end = _time.time() + 3
    while _time.time() < t_end and detail._loaded is None:
        app.processEvents()
        _time.sleep(0.02)
    assert detail._editors["level"].currentText() == "critical"
    table.stop()
    detail.stop()
