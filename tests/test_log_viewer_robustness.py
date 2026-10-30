"""Acceptance tests for the log viewer's three reported problems:

1. slow to open           -> load is bounded (days=), off the GUI thread,
                             and the list is model/view (cost ~ visible rows)
2. window far too wide    -> filters are popups of fixed width, the window
                             is sized from its parent/screen, never its content
3. crashes eco desktop    -> bad log data never raises; the desktop's menu
                             slot catches everything; default is out-of-process

Every test that *could* abort the interpreter (the Qt-slot crash) runs in a
child process, so a regression fails the test instead of killing pytest.
"""
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtWidgets

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.logs as logs
from eco.widgets import log_timeline_qt
from eco.widgets.log_timeline_common import TimelineEntry, bucket_density, bucket_density_sorted
from eco.widgets.log_timeline_qt import LogTimelineQt, initial_window_size

REPO = Path(__file__).resolve().parents[1]


_APP = []


def _app():
    # held in a module global: a QApplication nobody references is
    # garbage-collected, and the next QWidget() then aborts the interpreter
    if not _APP:
        _APP.append(QtWidgets.QApplication.instance() or QtWidgets.QApplication([]))
    return _APP[0]


def _write_session(log_dir, stamp, kind, sid, records, mtime=None):
    p = Path(log_dir) / f"{stamp}_{kind}_{sid}.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _many_sessions(log_dir, n_sessions, per_session, t_end=None):
    t_end = t_end or time.time()
    for s in range(n_sessions):
        t = t_end - (n_sessions - s) * 3600
        recs = [{"t": t, "event": "session_start", "kind": "desktop", "label": "bernina", "pid": s}]
        for i in range(per_session):
            recs.append({"t": t + i, "event": ("input", "result", "stream")[i % 3], "code": f"m.mv({i})", "text": "x"})
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(t))
        _write_session(log_dir, stamp, "desktop", f"{s:08x}", recs, mtime=t + per_session)


# -- 3. bad data never raises -------------------------------------------------


BAD_LINES = [
    '{"t": "2026-10-29T10:00", "event": "input", "code": "x"}',  # non-numeric t
    "[1, 2]",  # valid JSON, not a dict
    '"just a string"',
    "{not json",
    '{"t": null, "event": "input"}',  # dropped silently (not a timeline point)
    '{"t": 5, "event": "stream", "text": {"nested": 1}}',  # non-str text
    "",
]


def test_malformed_lines_are_skipped_and_counted(tmp_path):
    good = {"t": 1000.0, "event": "input", "code": "mono.mv(1)"}
    _write_session(tmp_path, "20261029_100000", "desktop", "aaaa1111", [good] + BAD_LINES)
    entries, report = logs.load_kernel_entries(log_dir=tmp_path)
    assert [e.text for e in entries if e.kind == "input"] == ["mono.mv(1)"]
    assert report.bad_lines == 4  # the 4 that raise; null-t and blank are not errors
    assert report.files_read == 1


def test_non_utf8_bytes_do_not_raise(tmp_path):
    p = _write_session(tmp_path, "20261029_100000", "desktop", "aaaa1111", [{"t": 1.0, "event": "input", "code": "a"}])
    with open(p, "ab") as f:
        f.write(b'{"t": 2.0, "event": "input", "code": "\xff\xfe"}\n')
    entries, report = logs.load_kernel_entries(log_dir=tmp_path)
    assert len([e for e in entries if e.kind == "input"]) == 2


def test_unreadable_file_is_reported_not_raised(tmp_path):
    (tmp_path / "20261029_100000_desktop_dir.jsonl").mkdir()  # IsADirectoryError on read
    _write_session(tmp_path, "20261029_110000", "desktop", "bbbb2222", [{"t": 1.0, "event": "input", "code": "a"}])
    entries, report = logs.load_kernel_entries(log_dir=tmp_path)
    assert len(report.files_failed) == 1
    assert [e.text for e in entries if e.kind == "input"] == ["a"]


def test_days_limits_entries_and_skips_old_files_by_mtime(tmp_path):
    now = time.time()
    old_t = now - 40 * 86400
    _write_session(tmp_path, "20250101_000000", "desktop", "old00000", [{"t": old_t, "event": "input", "code": "old"}], mtime=old_t)
    _write_session(tmp_path, "20261029_000000", "desktop", "new00000", [{"t": now - 60, "event": "input", "code": "new"}])
    entries, report = logs.load_kernel_entries(log_dir=tmp_path, days=14)
    assert [e.text for e in entries if e.kind == "input"] == ["new"]
    assert report.files_skipped_old == 1
    entries_all, _ = logs.load_kernel_entries(log_dir=tmp_path, days=None)
    assert {e.text for e in entries_all if e.kind == "input"} == {"old", "new"}


_SLOT_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {tests!r})
    from _light_eco import ensure_eco_importable; ensure_eco_importable()
    from qtpy import QtWidgets, QtCore
    app = QtWidgets.QApplication([])
    import eco.logs as logs
    {setup}
    action = QtWidgets.QAction(None)
    action.triggered.connect(lambda: {call})
    QtCore.QTimer.singleShot(0, action.trigger)
    QtCore.QTimer.singleShot(1500, app.quit)
    app.exec_()
    print("SURVIVED")
    """
)


def _run_slot_script(home, setup, call):
    env = dict(os.environ, HOME=str(home), QT_QPA_PLATFORM="offscreen")
    script = _SLOT_SCRIPT.format(tests=str(REPO / "tests"), setup=setup, call=call)
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60)


def test_bad_log_line_does_not_abort_a_qt_app(tmp_path):
    """The reported crash: eco desktop's Tools -> Log Viewer is a Qt slot;
    an exception escaping a PyQt slot is qFatal -> abort() (exit 134).
    Before the fix one malformed line in any session file did exactly that."""
    log_dir = tmp_path / ".eco" / "kernel_logs"
    log_dir.mkdir(parents=True)
    _write_session(log_dir, "20261029_100000", "desktop", "aaaa1111", [{"t": time.time(), "event": "input", "code": "a"}] + BAD_LINES)
    r = _run_slot_script(tmp_path, "", 'logs.widget(prefer="qt")')
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SURVIVED" in r.stdout


def test_desktop_menu_slot_survives_any_viewer_failure(tmp_path):
    """Even if eco.logs.widget itself raises (for whatever future reason),
    EcoDesktopApp._open_log_viewer must swallow it and show a message."""
    pytest.importorskip("qtconsole")
    setup = textwrap.dedent(
        """
        try:
            from eco.widgets import desktop_app
        except Exception as exc:
            print("SKIP", exc); sys.exit(0)
        def boom(*a, **k): raise RuntimeError("viewer exploded")
        logs.widget = boom
        QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: print("WARNED"))
        class Fake: pass
        fake = Fake(); fake._log_viewer = None; fake.window = None
        fake.LOG_VIEWER_MODE = "subprocess"
        """
    )
    r = _run_slot_script(tmp_path, setup, "desktop_app.EcoDesktopApp._open_log_viewer(fake)")
    if "SKIP" in r.stdout:
        pytest.skip("eco.widgets.desktop_app not importable here: " + r.stdout)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNED" in r.stdout and "SURVIVED" in r.stdout


# -- 2. width -------------------------------------------------------------------


def test_window_width_independent_of_number_of_sessions():
    _app()
    entries = [
        TimelineEntry(t=1000.0 + i, kind=f"kind{i % 12}", text="x", session=f"desktop:bernina · {i:06d}-abcd")
        for i in range(600)
    ]
    viewer = LogTimelineQt(entries)
    assert len(viewer.session_filter.popup.items) == 600
    # was ~280 px per session (>100000 px for 400 sessions)
    assert viewer.minimumSizeHint().width() < 900


class _Size:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


def test_initial_window_size_rules():
    assert initial_window_size() == log_timeline_qt.DEFAULT_SIZE
    # from the parent ("mother") window
    assert initial_window_size(_Size(2000, 1200)) == (1400, 840)
    # never more than the screen allows
    w, h = initial_window_size(_Size(5000, 3000), _Size(1920, 1080))
    assert w <= 1920 * log_timeline_qt.MAX_SCREEN_FRACTION and h <= 1080 * log_timeline_qt.MAX_SCREEN_FRACTION
    # tiny parent -> floor
    assert initial_window_size(_Size(100, 100)) == log_timeline_qt.MIN_SIZE


def test_status_text_cannot_widen_window():
    _app()
    viewer = LogTimelineQt([TimelineEntry(t=1.0, kind="input", text="a")])
    before = viewer.minimumSizeHint().width()
    viewer.set_status("x" * 5000)
    assert viewer.minimumSizeHint().width() == before


def test_huge_entry_text_is_truncated_in_the_list():
    _app()
    e = TimelineEntry(t=1.0, kind="result", text="1" * 100000)
    viewer = LogTimelineQt([e])
    row = viewer._row_of[id(e)]
    shown = viewer.model.data(viewer.model.index(row))
    assert len(shown) < log_timeline_qt._MAX_ROW_CHARS + 50


# -- 1. speed -------------------------------------------------------------------


def test_bucket_density_sorted_matches_reference():
    import random

    random.seed(1)
    entries = sorted(
        (TimelineEntry(t=random.uniform(0, 1e5), kind="input", text="", is_error=random.random() < 0.05) for _ in range(5000)),
        key=lambda e: e.t,
    )
    ts = [e.t for e in entries]
    ets = [e.t for e in entries if e.is_error]
    assert bucket_density_sorted(ts, ets, 1000.0, 90000.0, 140) == bucket_density(entries, 1000.0, 90000.0, 140)


@pytest.mark.parametrize("n", [100_000])
def test_building_the_viewer_scales(n):
    """400k entries took ~5.4 s to build before (one QListWidgetItem each);
    model/view keeps it about one Python pass. Generous bound for slow CI."""
    _app()
    entries = [TimelineEntry(t=1.7e9 + i * 7.0, kind=("input", "result", "stream")[i % 3], text=f"m.mv({i})", session=f"s{i % 50}") for i in range(n)]
    t = time.perf_counter()
    viewer = LogTimelineQt(entries)
    build = time.perf_counter() - t
    t = time.perf_counter()
    viewer.set_kind_visible("stream", False)
    refilter = time.perf_counter() - t
    assert build < 3.0, build
    assert refilter < 2.0, refilter


def test_window_shows_before_loading_finishes(tmp_path):
    """The loader runs on a background thread: the window exists (status
    'loading…') while a slow load is still going, then fills in."""
    app = _app()
    gate = {"go": False}

    def slow_loader():
        while not gate["go"]:
            time.sleep(0.01)
        return [TimelineEntry(t=1.0, kind="input", text="a")], "1 entry"

    win = log_timeline_qt.LogTimelineQtWindow(None, loader=slow_loader, auto_start=False)
    win._build_window()
    try:
        assert win.window is not None and win.viewer.status.text() == "loading…"
        gate["go"] = True
        deadline = time.time() + 5
        while time.time() < deadline and win.viewer.status.text() == "loading…":
            app.processEvents()
            time.sleep(0.01)
        assert win.viewer.status.text() == "1 entry"
        assert [e.text for e in win.viewer.visible_entries()] == ["a"]
    finally:
        win.stop()


def test_loader_exception_shows_in_status_not_raised():
    app = _app()

    def bad_loader():
        raise MemoryError("too much history")

    win = log_timeline_qt.LogTimelineQtWindow(None, loader=bad_loader, auto_start=False)
    win._build_window()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and "failed" not in win.viewer.status.text():
            app.processEvents()
            time.sleep(0.01)
        assert "MemoryError" in win.viewer.status.text()
    finally:
        win.stop()


# -- out-of-process viewer ---------------------------------------------------------


def test_subprocess_viewer_starts_and_stops(tmp_path, monkeypatch):
    """eco desktop's default: the viewer is a child process (see
    eco.widgets.log_viewer_app). It must come up without importing the
    whole of eco, stay up, and go away on stop()."""
    from eco.widgets import log_viewer_app

    log_dir = tmp_path / ".eco" / "kernel_logs"
    log_dir.mkdir(parents=True)
    _many_sessions(log_dir, 3, 20)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    t = time.perf_counter()
    proc = log_viewer_app.launch_subprocess(days=14, log_path=tmp_path / "viewer.log")
    try:
        time.sleep(3)
        assert proc.is_running(), (tmp_path / "viewer.log").read_text()
    finally:
        proc.stop()
    assert not proc.is_running()
    out = (tmp_path / "viewer.log").read_text()
    assert "Traceback" not in out, out
