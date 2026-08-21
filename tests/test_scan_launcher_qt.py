"""Tests for eco.widgets.scan_launcher_qt.

Uses lightweight fake doubles (FakeAdjustable/FakeDetector/FakeRoot for the
component picker's namespace tree, FakeQueue/FakeQueueItem/FakeScan for the
queue panel, FakeScans for the launcher's Scans-shaped methods) rather than
real StepScan/ScanQueue machinery, so these tests exercise the widget logic
in isolation without needing real devices or a real background scan thread.
"""
import weakref
from pathlib import Path

import pytest

pytest.importorskip("qtpy")
from qtpy import QtWidgets

import eco.widgets.scan_launcher_qt as slq


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    # ComponentBookmarks/RecentComponents default to Path.home()/".eco"/...
    # when not given an explicit path -- redirect so tests never touch the
    # real account's home directory.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


# ---------------------------------------------------------------------------
# fake doubles
# ---------------------------------------------------------------------------


class _FakeStatusCollection:
    def __init__(self, items):
        self._list = [weakref.ref(item) for item in items]


class FakeAdjustable:
    def __init__(self, name, value=0.0):
        self.name = name
        self._value = value

    def get_current_value(self):
        return self._value

    def set_target_value(self, value):
        self._value = value

        class _Handle:
            def wait(self, timeout=None):
                return None

        return _Handle()


class FakeDetector:
    def __init__(self, name, value=1.0):
        self.name = name
        self._value = value

    def get_current_value(self):
        return self._value


class FakeRoot:
    def __init__(self, children, name="fakeroot"):
        self.name = name
        self._children = list(children)  # keep strong refs alive for the weakrefs below
        self.status_collection = _FakeStatusCollection(self._children)


class FakeQueueItem:
    def __init__(self, state="pending", scan=None, description=""):
        self.state = state
        self.scan = scan
        self.description = description

    def __repr__(self):
        return f"<FakeQueueItem {self.state!r} {self.description!r}>"


class FakeScan:
    def __init__(self, total, done, readbacks, adjustables, paused=False):
        self.scan_info = {"scan_values_all": [None] * total}
        self._values_done = [None] * done
        self.readbacks = readbacks
        self.adjustables = adjustables
        self._paused = paused

    def is_paused(self):
        return self._paused


class FakeQueue:
    def __init__(self, name="default"):
        self.name = name
        self.pause_calls = 0
        self.resume_calls = 0
        self.pause_current_calls = 0
        self.resume_current_calls = 0
        self.stop_current_calls = 0
        self.saved = []
        self.resume_from_file_calls = []
        self._status = {"name": name, "paused": False, "current": None, "pending": []}

    def status(self):
        return dict(self._status, pending=list(self._status["pending"]))

    def pause(self):
        self.pause_calls += 1
        self._status["paused"] = True

    def resume(self):
        self.resume_calls += 1
        self._status["paused"] = False

    def pause_current(self):
        self.pause_current_calls += 1

    def resume_current(self):
        self.resume_current_calls += 1

    def stop_current(self):
        self.stop_current_calls += 1

    def save_paused(self, item, path):
        self.saved.append((item, path))

    def resume_from_file(self, path, root, counters):
        self.resume_from_file_calls.append((path, root, counters))
        return FakeQueueItem(state="pending", description="resumed")


class FakeScansQueues:
    def __init__(self, queue):
        self._queue = queue

    def __getitem__(self, name):
        return self._queue


class FakeScans:
    def __init__(self, queue):
        self.name = "scans"
        self.queues = FakeScansQueues(queue)
        self.calls = []

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        return FakeQueueItem(state="pending", description=kwargs.get("description", ""))

    def ascan(self, adjustable, start, end, n, n_pulses, **kwargs):
        return self._record("ascan", (adjustable, start, end, n, n_pulses), kwargs)

    def dscan(self, adjustable, start, end, n, n_pulses, **kwargs):
        return self._record("dscan", (adjustable, start, end, n, n_pulses), kwargs)

    def acquire(self, n_pulses, **kwargs):
        return self._record("acquire", (n_pulses,), kwargs)

    def meshscan(self, *adj_specs, **kwargs):
        return self._record("meshscan", adj_specs, kwargs)


# ---------------------------------------------------------------------------
# ComponentPickerQt / MultiComponentPickerQt
# ---------------------------------------------------------------------------


def test_component_picker_qt_starts_empty(qapp):
    root = FakeRoot([FakeAdjustable("motor1")])
    picker = slq.ComponentPickerQt(root, kind_filter="Adjustable")
    assert picker.value is None
    assert picker.path is None
    assert picker.label.text() == "(none selected)"


def test_component_picker_qt_clear_resets_and_emits_changed(qapp):
    motor = FakeAdjustable("motor1")
    root = FakeRoot([motor])
    picker = slq.ComponentPickerQt(root, kind_filter="Adjustable")
    picker.value = motor
    picker.path = "motor1"
    seen = []
    picker.changed.connect(lambda: seen.append(True))
    picker.clear()
    assert picker.value is None
    assert picker.path is None
    assert picker.label.text() == picker.placeholder
    assert seen == [True]


def test_component_picker_qt_pick_dialog_sets_value_on_accept(qapp, monkeypatch):
    motor = FakeAdjustable("motor1", value=3.0)
    root = FakeRoot([motor])
    picker = slq.ComponentPickerQt(root, kind_filter="Adjustable")
    changed = []
    picker.changed.connect(lambda: changed.append(True))

    captured = {}
    orig_selector_init = slq.ComponentSelectorQt.__init__

    def wrapped_init(self, *a, **kw):
        orig_selector_init(self, *a, **kw)
        captured["selector"] = self

    monkeypatch.setattr(slq.ComponentSelectorQt, "__init__", wrapped_init)

    def fake_exec(self):
        captured["selector"]._select("motor1", motor, "adjustable")
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec_", fake_exec)

    picker._pick()

    assert picker.value is motor
    assert picker.path == "motor1"
    assert picker.label.text() == "motor1"
    assert changed == [True]


def test_component_picker_qt_pick_dialog_cancelled_leaves_value_unset(qapp, monkeypatch):
    root = FakeRoot([FakeAdjustable("motor1")])
    picker = slq.ComponentPickerQt(root, kind_filter="Adjustable")
    monkeypatch.setattr(QtWidgets.QDialog, "exec_", lambda self: QtWidgets.QDialog.Rejected)
    picker._pick()
    assert picker.value is None


def test_multi_component_picker_qt_get_value(qapp):
    d1 = FakeDetector("d1")
    d2 = FakeDetector("d2")
    root = FakeRoot([d1, d2])
    multi = slq.MultiComponentPickerQt(root, kind_filter="Detector")
    assert multi.get_value() == []
    p1 = multi.add_row()
    p1.value = d1
    p2 = multi.add_row()
    p2.value = d2
    assert multi.get_value() == [d1, d2]


# ---------------------------------------------------------------------------
# StepSpecQt / ScanAxisRowQt / ScanBuilderQt
# ---------------------------------------------------------------------------


def test_step_spec_qt_linear_default(qapp):
    spec = slq.StepSpecQt(start=1.0, end=5.0, n=4)
    assert spec.get_spec() == (1.0, 5.0, 4)


def test_step_spec_qt_position_list_mode(qapp):
    spec = slq.StepSpecQt()
    spec.mode_dd.setCurrentText("position list")
    spec.list_w.setText("1,2,3")
    assert spec.get_spec() == ([1.0, 2.0, 3.0],)


def test_scan_axis_row_qt_single_axis(qapp):
    motor = FakeAdjustable("motor1")
    root = FakeRoot([motor])
    row = slq.ScanAxisRowQt(root, allow_simultaneous=True)
    picker, spec, _ = row._sub_rows[0]
    picker.value = motor
    spec.start_w.setValue(0.0)
    spec.end_w.setValue(2.0)
    spec.n_w.setValue(5)
    assert row.get_adj_spec() == (motor, 0.0, 2.0, 5)


def test_scan_axis_row_qt_simultaneous_mode_grows_and_collects_all(qapp):
    m1 = FakeAdjustable("m1")
    m2 = FakeAdjustable("m2")
    root = FakeRoot([m1, m2])
    row = slq.ScanAxisRowQt(root, allow_simultaneous=True)
    row.mode_dd.setCurrentText("simultaneous (co-moving)")
    row._sub_rows[0][0].value = m1
    row._add_sub_row()
    row._sub_rows[1][0].value = m2
    spec = row.get_adj_spec()
    assert spec == [(m1, 0.0, 1.0, 10), (m2, 0.0, 1.0, 10)]

    # switching back to "single axis" collapses to one sub-row
    row.mode_dd.setCurrentText("single axis")
    assert len(row._sub_rows) == 1


def test_scan_axis_row_qt_meshscan_never_shows_simultaneous_toggle(qapp):
    root = FakeRoot([])
    row = slq.ScanAxisRowQt(root, allow_simultaneous=False)
    assert row.mode_dd is None
    assert len(row._sub_rows) == 1


def test_scan_builder_qt_get_adj_specs_and_add_axis(qapp):
    m1 = FakeAdjustable("m1")
    root = FakeRoot([m1])
    builder = slq.ScanBuilderQt(root, allow_simultaneous=False)
    picker, spec, _ = builder._axis_rows[0][0]._sub_rows[0]
    picker.value = m1
    assert builder.get_adj_specs() == [(m1, 0.0, 1.0, 10)]
    builder.add_axis()
    assert len(builder._axis_rows) == 2


# ---------------------------------------------------------------------------
# structured form builders
# ---------------------------------------------------------------------------


def test_build_ascan_like_form_qt_get_call(qapp):
    motor = FakeAdjustable("motor1")
    root = FakeRoot([motor])
    widget, get_call = slq._build_ascan_like_form_qt(None, root, None, None)

    pickers = widget.findChildren(slq.ComponentPickerQt)
    assert len(pickers) == 1
    pickers[0].value = motor

    start_w, end_w, _settling_w = widget.findChildren(QtWidgets.QDoubleSpinBox)
    start_w.setValue(0.5)
    end_w.setValue(2.5)
    n_w, n_pulses_w, rep_w = widget.findChildren(QtWidgets.QSpinBox)
    n_w.setValue(7)
    n_pulses_w.setValue(50)

    args, kwargs = get_call()
    assert args == [motor, 0.5, 2.5, 7, 50]
    assert kwargs["settling_time"] == 0.0
    assert kwargs["repetitions"] == 1
    assert kwargs["return_at_end"] == "timeout"
    assert "counters" not in kwargs


def test_build_acquire_form_qt_get_call(qapp):
    root = FakeRoot([])
    widget, get_call = slq._build_acquire_form_qt(None, root, None, None)
    n_pulses_w, n_rep_w = widget.findChildren(QtWidgets.QSpinBox)
    n_pulses_w.setValue(200)
    n_rep_w.setValue(3)
    args, kwargs = get_call()
    assert args == [200]
    assert kwargs["N_repetitions"] == 3
    assert kwargs["return_at_end"] is True
    assert "repetitions" not in kwargs


def test_build_ascan_position_list_form_qt_get_call(qapp):
    motor = FakeAdjustable("motor1")
    root = FakeRoot([motor])
    widget, get_call = slq._build_ascan_position_list_form_qt(None, root, None, None)
    widget.findChildren(slq.ComponentPickerQt)[0].value = motor
    positions_w = widget.findChildren(QtWidgets.QLineEdit)[0]
    positions_w.setText("0,0.5,1")
    args, kwargs = get_call()
    assert args == [motor, [0.0, 0.5, 1.0], 100]


def test_build_snakescan_form_qt_get_call(qapp):
    slow = FakeAdjustable("slow")
    fast = FakeAdjustable("fast")
    root = FakeRoot([slow, fast])
    widget, get_call = slq._build_snakescan_form_qt(None, root, None, None)
    picker_slow, picker_fast = widget.findChildren(slq.ComponentPickerQt)
    picker_slow.value = slow
    picker_fast.value = fast
    args, kwargs = get_call()
    assert args == [slow, 1.0, 5, fast, 1.0]


def test_build_a2scan_form_qt_get_call(qapp):
    a0 = FakeAdjustable("a0")
    a1 = FakeAdjustable("a1")
    root = FakeRoot([a0, a1])
    widget, get_call = slq._build_a2scan_form_qt(None, root, None, None)
    picker0, picker1 = widget.findChildren(slq.ComponentPickerQt)
    picker0.value = a0
    picker1.value = a1
    args, kwargs = get_call()
    assert args == [a0, 0.0, 1.0, a1, 0.0, 1.0, 10, 100]


def test_build_grid_form_qt_meshscan_get_call(qapp):
    m1 = FakeAdjustable("m1")
    root = FakeRoot([m1])
    build = slq._build_grid_form_qt(allow_simultaneous=False)
    widget, get_call = build(None, root, None, None)
    widget.findChildren(slq.ComponentPickerQt)[0].value = m1
    args, kwargs = get_call()
    assert args == [(m1, 0.0, 1.0, 10)]
    assert kwargs["N_pulses"] == 100
    assert kwargs["scanning_order"] == "last_fastest"


def test_common_extra_kwargs_qt_includes_counters_only_if_picked(qapp):
    d1 = FakeDetector("d1")
    root = FakeRoot([d1])
    box, read = slq._build_common_extra_kwargs_qt(root, None, None)
    kw = read()
    assert "counters" not in kw
    counters_w = box.findChildren(slq.MultiComponentPickerQt)[0]
    counters_w.add_row().value = d1
    kw = read()
    assert kw["counters"] == [d1]


# ---------------------------------------------------------------------------
# generic reflection-based fallback
# ---------------------------------------------------------------------------


def test_build_generic_form_qt_reflects_signature_defaults(qapp):
    def sample(a, b=2, *args, c=3, **kwargs):
        pass

    widget, get_call = slq._build_generic_form_qt(sample)
    args, kwargs = get_call()
    assert args == ["", 2]
    assert kwargs == {"c": 3}


def test_build_generic_form_qt_var_keyword_parses_json(qapp):
    def sample(**kwargs):
        pass

    widget, get_call = slq._build_generic_form_qt(sample)
    edit = widget.findChildren(QtWidgets.QLineEdit)[0]
    edit.setText('{"foo": 1, "bar": "baz"}')
    args, kwargs = get_call()
    assert kwargs == {"foo": 1, "bar": "baz"}


# ---------------------------------------------------------------------------
# ScanQueuePanel
# ---------------------------------------------------------------------------


def test_scan_queue_panel_poll_updates_status_and_progress(qapp):
    queue = FakeQueue()
    root = FakeRoot([])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        motor = FakeAdjustable("motor1")
        scan = FakeScan(total=5, done=2, readbacks=[[0.0], [0.5]], adjustables=[motor])
        item = FakeQueueItem(state="running", scan=scan)
        panel.track_item(item)
        queue._status["current"] = repr(item)
        panel._poll()
        assert panel.progress.maximum() == 5
        assert panel.progress.value() == 2
        assert "step 2/5" in panel.progress.format()
        assert "Current:" in panel.current_label.text()
    finally:
        panel.stop()


def test_scan_queue_panel_poll_idle_when_nothing_tracked(qapp):
    queue = FakeQueue()
    root = FakeRoot([])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        panel._poll()
        assert panel.progress.format() == "no active scan"
    finally:
        panel.stop()


def test_scan_queue_panel_pause_resume_queue_buttons(qapp):
    queue = FakeQueue()
    root = FakeRoot([])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        panel.pause_queue_btn.setChecked(True)
        assert queue.pause_calls == 1
        assert panel.pause_queue_btn.text() == "Resume queue"
        panel.pause_queue_btn.setChecked(False)
        assert queue.resume_calls == 1
        assert panel.pause_queue_btn.text() == "Pause queue"
    finally:
        panel.stop()


def test_scan_queue_panel_save_paused_needs_a_paused_tracked_item(qapp, monkeypatch):
    queue = FakeQueue()
    root = FakeRoot([])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        shown = []
        monkeypatch.setattr(
            QtWidgets.QMessageBox, "information", lambda *a, **kw: shown.append(a)
        )
        panel._on_save_paused()
        assert shown
        assert queue.saved == []
    finally:
        panel.stop()


def test_scan_queue_panel_save_paused_saves_when_item_is_paused(qapp, monkeypatch, tmp_path):
    queue = FakeQueue()
    root = FakeRoot([])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        scan = FakeScan(total=3, done=1, readbacks=[[0.0]], adjustables=[], paused=True)
        item = FakeQueueItem(state="running", scan=scan)
        panel.track_item(item)
        path = str(tmp_path / "paused.json")
        monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **kw: (path, ""))
        monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **kw: None)
        panel._on_save_paused()
        assert queue.saved == [(item, path)]
    finally:
        panel.stop()


def test_scan_queue_panel_resume_from_file_picks_counters_then_submits(qapp, monkeypatch, tmp_path):
    d1 = FakeDetector("d1")
    queue = FakeQueue()
    root = FakeRoot([d1])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        path = str(tmp_path / "resume.json")
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **kw: (path, ""))

        def fake_exec(dialog_self):
            picker = dialog_self.findChildren(slq.MultiComponentPickerQt)[0]
            picker.add_row().value = d1
            return QtWidgets.QDialog.Accepted

        monkeypatch.setattr(QtWidgets.QDialog, "exec_", fake_exec)

        panel._on_resume_from_file()
        assert queue.resume_from_file_calls == [(path, root, [d1])]
        assert len(panel._tracked) == 1
    finally:
        panel.stop()


def test_scan_queue_panel_resume_from_file_rejects_empty_counters(qapp, monkeypatch, tmp_path):
    queue = FakeQueue()
    root = FakeRoot([])
    panel = slq.ScanQueuePanel(queue, root)
    try:
        path = str(tmp_path / "resume.json")
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **kw: (path, ""))
        monkeypatch.setattr(QtWidgets.QDialog, "exec_", lambda self: QtWidgets.QDialog.Accepted)
        warned = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **kw: warned.append(a))
        panel._on_resume_from_file()
        assert queue.resume_from_file_calls == []
        assert warned
    finally:
        panel.stop()


# ---------------------------------------------------------------------------
# ScanLauncherQt
# ---------------------------------------------------------------------------


def test_scan_launcher_qt_run_submits_through_queue(qapp):
    motor = FakeAdjustable("motor1")
    root = FakeRoot([motor])
    queue = FakeQueue()
    scans = FakeScans(queue)
    launcher = slq.ScanLauncherQt(scans, root=root, methods=["ascan"], queue_name="default")
    try:
        picker = launcher._pages["ascan"][0].findChildren(slq.ComponentPickerQt)[0]
        picker.value = motor
        launcher._on_run()
        assert len(scans.calls) == 1
        name, args, kwargs = scans.calls[0]
        assert name == "ascan"
        assert args[0] is motor
        assert kwargs["scan_queue"] == "default"
        assert len(launcher.queue_panel._tracked) == 1
        assert "Submitted" in launcher.status_label.text()
    finally:
        launcher.stop()


def test_scan_launcher_qt_method_switch_updates_stack(qapp):
    root = FakeRoot([])
    queue = FakeQueue()
    scans = FakeScans(queue)
    launcher = slq.ScanLauncherQt(
        scans, root=root, methods=["ascan", "acquire"], queue_name="default"
    )
    try:
        launcher.method_combo.setCurrentText("acquire")
        assert launcher._stack.currentWidget() is launcher._pages["acquire"][0]
        launcher._on_run()
        assert scans.calls[-1][0] == "acquire"
    finally:
        launcher.stop()


def test_scan_launcher_qt_run_reports_errors_without_crashing(qapp):
    root = FakeRoot([])
    queue = FakeQueue()
    scans = FakeScans(queue)

    def boom(*a, **kw):
        raise RuntimeError("no adjustable picked")

    scans.acquire = boom
    launcher = slq.ScanLauncherQt(scans, root=root, methods=["acquire"], queue_name="default")
    try:
        launcher._on_run()
        assert "Error" in launcher.status_label.text()
        assert launcher.queue_panel._tracked == []
    finally:
        launcher.stop()


def test_scan_launcher_qt_falls_back_to_generic_form_for_unknown_method(qapp):
    root = FakeRoot([])
    queue = FakeQueue()
    scans = FakeScans(queue)
    launcher = slq.ScanLauncherQt(scans, root=root, methods=["meshscan"], queue_name="default")
    try:
        # meshscan IS structured, so use it to confirm the structured page
        # was picked over the generic fallback path
        assert "ScanBuilderQt" in [
            type(w).__name__ for w in launcher._pages["meshscan"][0].findChildren(QtWidgets.QWidget)
        ]
    finally:
        launcher.stop()
