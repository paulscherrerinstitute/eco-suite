"""Coverage for LogTimelineQt's kind-filter checkboxes, multi/range
selection, and "copy selected as script" -- constructing a real
LogTimelineQt has been confirmed safe under pytest+offscreen (unlike some
of eco's other real-Qt-widget constructions -- see
tests/test_indicator_widgets.py's docstring for that fragility), verified
manually before writing this file.
"""
import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtWidgets

from _light_eco import ensure_eco_importable

ensure_eco_importable()

from eco.widgets.log_timeline_common import TimelineEntry
from eco.widgets.log_timeline_qt import LogTimelineQt


def _entries():
    return [
        TimelineEntry(t=1000.0, kind="input", text="mono.get_current_value()"),
        TimelineEntry(t=1000.2, kind="result", text="12000.0"),
        TimelineEntry(t=1005.0, kind="widget_control", text="cam_west.widget()"),
        TimelineEntry(t=1010.0, kind="input", text="att.set_target_value(0.5)"),
        TimelineEntry(t=1010.1, kind="stream", text="[stdout] moving..."),
        TimelineEntry(t=1015.0, kind="error", text="RuntimeError: timeout"),
    ]


def _select_all_visible(viewer):
    viewer.select_entries(viewer.visible_entries())


def _visible_kinds(viewer):
    return {e.kind for e in viewer.visible_entries()}


def test_filter_checkboxes_cover_every_distinct_kind():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    assert set(viewer.kind_filter.popup.items) == {
        "input", "result", "widget_control", "stream", "error",
    }
    assert viewer.kind_filter.popup.checked() == set(viewer.kind_filter.popup.items)


def test_unchecking_a_kind_hides_only_its_rows():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")

    viewer.set_kind_visible("stream", False)
    viewer.set_kind_visible("result", False)

    assert _visible_kinds(viewer) == {"input", "widget_control", "error"}


def test_session_row_absent_without_any_per_entry_session():
    """A pure scilog timeline has no entry.session at all -- nothing to
    filter by, so the Stream row shouldn't be built."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    assert viewer.session_filter.popup.items == {}
    assert viewer.session_filter.isHidden()


def _multi_stream_entries():
    return [
        TimelineEntry(t=1000.0, kind="input", text="mono.get_current_value()", session="desktop:bernina"),
        TimelineEntry(t=1000.2, kind="result", text="12000.0", session="desktop:bernina"),
        TimelineEntry(t=1010.0, kind="input", text="att.set_target_value(0.5)", session="console:bernina"),
        TimelineEntry(t=1010.1, kind="stream", text="[stdout] moving...", session="console:bernina"),
    ]


def test_session_row_covers_every_distinct_stream():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_multi_stream_entries(), title="test")
    assert set(viewer.session_filter.popup.items) == {"desktop:bernina", "console:bernina"}
    assert viewer.session_filter.popup.checked() == set(viewer.session_filter.popup.items)


def test_unchecking_a_stream_hides_only_its_rows_and_combines_with_kind_filter():
    """Kind and Stream filters are AND-combined -- separable but still
    mergeable by default (both rows start fully checked)."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_multi_stream_entries(), title="test")

    viewer.set_session_visible("desktop:bernina", False)
    visible_sessions = {e.session for e in viewer.visible_entries()}
    assert visible_sessions == {"console:bernina"}
    # console:bernina has both "input" and "stream" entries -- both kinds
    # still checked, so both are still visible once the desktop stream is
    # filtered out.
    assert _visible_kinds(viewer) == {"input", "stream"}

    # AND-combine: also uncheck the "stream" kind -- only console's
    # "input" row should remain visible.
    viewer.set_kind_visible("stream", False)
    assert _visible_kinds(viewer) == {"input"}


def test_rechecking_shows_rows_again():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")

    viewer.set_kind_visible("error", False)
    assert "error" not in _visible_kinds(viewer)
    viewer.set_kind_visible("error", True)
    assert "error" in _visible_kinds(viewer)


def test_list_selection_mode_allows_multi_select():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    assert viewer.list.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection


def test_copy_selected_as_script_without_timing():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    _select_all_visible(viewer)
    viewer.timing_checkbox.setChecked(False)

    viewer._copy_selected_as_script()
    script = QtWidgets.QApplication.clipboard().text()

    assert "mono.get_current_value()" in script
    assert "cam_west.widget()" in script
    assert "att.set_target_value(0.5)" in script
    assert "# [result]" in script
    assert "# [stream]" in script
    assert "# [error]" in script
    assert "time.sleep" not in script
    assert "import time" not in script


def test_copy_selected_as_script_with_timing_inserts_real_gaps():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    _select_all_visible(viewer)
    viewer.timing_checkbox.setChecked(True)

    viewer._copy_selected_as_script()
    script = QtWidgets.QApplication.clipboard().text()

    assert script.startswith("import time\n")
    # mono -> cam_west is a 5s gap (1005.0 - 1000.0), skipping the 0.2s
    # mono->result gap (below the 0.05s "negligible" threshold is not the
    # point here -- 0.2s *is* above it, but result isn't code, so no sleep
    # is inserted purely between two comment lines either; what matters is
    # a real, selected code-to-code/comment gap shows up as a real sleep):
    assert "time.sleep(5.000)" in script


def test_copy_selected_as_script_only_uses_visible_selection():
    """Filtered-out entries must not leak into the script (they aren't
    rows of the model at all, so they can't be selected)."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    viewer.set_kind_visible("error", False)  # hide the error row

    # try to select every entry, hidden or not
    viewer.select_entries(viewer.entries)
    viewer.timing_checkbox.setChecked(False)

    viewer._copy_selected_as_script()
    script = QtWidgets.QApplication.clipboard().text()

    assert "timeout" not in script


def test_copy_selected_as_script_with_nothing_selected_sets_status():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    viewer.list.clearSelection()

    viewer._copy_selected_as_script()

    assert "nothing selected" in viewer.script_status.text()


def test_copy_selected_as_script_status_reports_counts():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = LogTimelineQt(_entries(), title="test")
    _select_all_visible(viewer)
    viewer.timing_checkbox.setChecked(False)

    viewer._copy_selected_as_script()

    assert "copied 6 entries" in viewer.script_status.text()
