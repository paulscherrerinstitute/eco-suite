import time

import pytest

pytest.importorskip("qtpy")

from qtpy import QtCore, QtWidgets

from eco.widgets.indicator_widgets import (
    INDICATOR_TYPES,
    AnalogGauge,
    BarGauge,
    Dial,
    LEDIndicator,
    NumericTile,
    Slider,
    StripChart,
    _EnumDialFace,
    _enum_index,
    _nearest_enum_position,
    _format_value,
    _guess_range,
    _sanitize_object_name,
    RangeDialog,
    range_around,
    attach_indicator_menu,
    create_indicator,
    item_path,
    resolve_item_path,
)


class _FakeDetector:
    """Read-only: no set_target_value."""

    def __init__(self, name, value=0.0):
        self.name = name
        self._value = value

    def get_current_value(self):
        return self._value


class _FakeAdjustable(_FakeDetector):
    def __init__(self, name, value=0.0, low_limit=None, high_limit=None):
        super().__init__(name, value)
        self.set_calls = []
        if low_limit is not None:
            self.low_limit = low_limit
        if high_limit is not None:
            self.high_limit = high_limit

    def set_target_value(self, value):
        self.set_calls.append(value)
        self._value = value


class _FakeEnumAdjustable(_FakeAdjustable):
    """AdjustableEnum-shaped: adds enum_strs on top of _FakeAdjustable."""

    def __init__(self, name, enum_strs, value=0):
        super().__init__(name, value)
        self.enum_strs = list(enum_strs)


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pump(app, predicate, timeout=5.0, interval=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False


# -- pure logic --


def test_format_value_numeric():
    assert _format_value(3.14159) == "3.142"


def test_format_value_non_numeric_falls_back_to_str():
    assert _format_value("abc") == "abc"


def test_guess_range_low_high_limit_attrs():
    item = _FakeAdjustable("m1", low_limit=-10, high_limit=10)
    assert _guess_range(item) == (-10.0, 10.0)


def test_guess_range_falls_back_to_default():
    item = _FakeDetector("d1")
    assert _guess_range(item) == (0.0, 100.0)


def test_guess_range_ignores_inverted_limits():
    item = _FakeAdjustable("m1", low_limit=10, high_limit=-10)
    assert _guess_range(item) == (0.0, 100.0)


def test_create_indicator_unknown_kind_raises():
    with pytest.raises(ValueError):
        create_indicator("not-a-real-kind", _FakeDetector("d1"))


def test_create_indicator_dispatches_to_the_right_factory_with_guessed_range(monkeypatch):
    # exercises create_indicator's own dispatch/range-guessing logic
    # against a lightweight fake factory rather than a real QDial-backed
    # Dial -- constructing an actual QDial here proved flaky (a native
    # crash, reproducible only under this specific combination of pytest
    # + the offscreen Qt platform + eco's full heavy scientific-stack
    # imports all in one process -- never in standalone real usage, and
    # not specific to Dial's own logic, which test_dial_* below cover
    # directly via real widgets built the same way create_indicator
    # would build them)
    calls = []

    class FakeWidget:
        def __init__(self, item, title=None, vmin=None, vmax=None, **kwargs):
            calls.append((item, title, vmin, vmax, kwargs))

        def setAccessibleName(self, name):
            pass

        def setObjectName(self, name):
            pass

    import eco.widgets.indicator_widgets as iw

    monkeypatch.setattr(
        iw,
        "INDICATOR_TYPES",
        (("Dial / knob", FakeWidget, True), ("LED", FakeWidget, False)),
    )

    item = _FakeAdjustable("m1", low_limit=0, high_limit=50)
    result = create_indicator("Dial / knob", item, title="M1")

    assert isinstance(result, FakeWidget)
    assert calls == [(item, "M1", 0.0, 50.0, {})]


def test_create_indicator_no_range_needed_skips_guessing(monkeypatch):
    calls = []

    class FakeWidget:
        def __init__(self, item, title=None, **kwargs):
            calls.append((item, title, kwargs))

        def setAccessibleName(self, name):
            pass

        def setObjectName(self, name):
            pass

    import eco.widgets.indicator_widgets as iw

    monkeypatch.setattr(iw, "INDICATOR_TYPES", (("LED", FakeWidget, False),))

    item = _FakeDetector("d1")
    create_indicator("LED", item, title="D1")

    assert calls == [(item, "D1", {})]


# -- polling / rendering, per gadget type --


def test_led_indicator_reflects_boolean_state():
    app = _app()
    item = _FakeDetector("shutter", value=True)
    w = LEDIndicator(item=item, title="Shutter", poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._is_on is True)
    finally:
        w.stop()


def test_led_indicator_threshold_mode():
    app = _app()
    item = _FakeDetector("intensity", value=5.0)
    w = LEDIndicator(item=item, title="Intensity", threshold=3.0, poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._is_on is True)
    finally:
        w.stop()


def test_led_indicator_click_toggles_settable_item():
    app = _app()
    item = _FakeAdjustable("shutter", value=False)
    w = LEDIndicator(item=item, title="Shutter", poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._is_on is False)
        w._on_click(None)
        assert _pump(app, lambda: item.set_calls == [True])
    finally:
        w.stop()


def test_led_indicator_click_is_noop_for_readonly_item():
    app = _app()
    item = _FakeDetector("readonly", value=False)
    w = LEDIndicator(item=item, title="RO", poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._is_on is False)
        w._on_click(None)  # should not raise, no set_target_value to call
    finally:
        w.stop()


def test_bar_gauge_renders_fraction_of_range():
    app = _app()
    item = _FakeDetector("d1", value=25.0)
    w = BarGauge(item=item, title="D1", vmin=0.0, vmax=50.0, poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._bar.value() == 500)  # 25/50 -> halfway of 0..1000
    finally:
        w.stop()


def test_bar_gauge_clips_out_of_range_values():
    app = _app()
    item = _FakeDetector("d1", value=999.0)
    w = BarGauge(item=item, title="D1", vmin=0.0, vmax=50.0, poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._bar.value() == 1000)
    finally:
        w.stop()


def test_analog_gauge_tracks_value():
    app = _app()
    item = _FakeDetector("d1", value=10.0)
    w = AnalogGauge(item=item, title="D1", vmin=0.0, vmax=20.0, poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._value == 10.0)
    finally:
        w.stop()


def test_strip_chart_accumulates_values():
    app = _app()
    item = _FakeDetector("d1", value=1.0)
    w = StripChart(item=item, title="D1", window_points=5, poll_interval=0.02)
    try:
        assert _pump(app, lambda: len(w._values) >= 3)
    finally:
        w.stop()


def test_strip_chart_window_is_bounded():
    app = _app()
    item = _FakeDetector("d1", value=1.0)
    w = StripChart(item=item, title="D1", window_points=5, poll_interval=0.01)
    try:
        assert _pump(app, lambda: len(w._values) == 5, timeout=3.0)
        time.sleep(0.1)
        app.processEvents()
        assert len(w._values) <= 5
    finally:
        w.stop()


def test_numeric_tile_formats_value():
    app = _app()
    item = _FakeDetector("d1", value=3.14159)
    w = NumericTile(item=item, title="D1", poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._number_label.text() == "3.142")
    finally:
        w.stop()


def test_dial_settable_writes_on_release():
    app = _app()
    item = _FakeAdjustable("m1", value=0.0, low_limit=0, high_limit=10)
    w = Dial(item=item, title="M1", vmin=0.0, vmax=10.0, step=1.0, poll_interval=0.02)
    try:
        assert w.settable is True
        assert w._dial.isEnabled()
        w._dial.setValue(5)
        w._on_release()
        assert item.set_calls == [5.0]
    finally:
        w.stop()


def test_dial_readonly_for_non_settable_item():
    app = _app()
    item = _FakeDetector("d1", value=3.0)
    w = Dial(item=item, title="D1", vmin=0.0, vmax=10.0, poll_interval=0.02)
    try:
        assert w.settable is False
        assert not w._dial.isEnabled()
    finally:
        w.stop()


# -- Dial enum mode (mode-select rotary knob) --


def test_enum_index_from_int():
    assert _enum_index(1, ["OFF", "ON", "AUTO"]) == 1


def test_enum_index_from_enum_member():
    import enum

    class Mode(enum.Enum):
        OFF = 0
        ON = 1

    assert _enum_index(Mode.ON, ["OFF", "ON"]) == 1


def test_enum_index_from_matching_string():
    assert _enum_index("AUTO", ["OFF", "ON", "AUTO"]) == 2


def test_enum_index_out_of_range_or_unmatched_returns_none():
    assert _enum_index(5, ["OFF", "ON"]) is None
    assert _enum_index("UNKNOWN", ["OFF", "ON"]) is None
    assert _enum_index(None, ["OFF", "ON"]) is None


# _EnumDialFace's geometry (_angle_for, _nearest_enum_position) is tested
# here as plain functions/attributes, via __new__() rather than a real
# constructed instance -- constructing a real one right after another
# test's Dial poll-thread is still mid-shutdown has been observed to
# crash the interpreter outright (the same pytest+offscreen+heavy-import
# native fragility already noted for QDial construction elsewhere in this
# file); the real, fully-constructed article is still exercised for real
# by the test_dial_* integration tests below (Dial._build_body() always
# builds one, for both numeric and enum items).


def _bare_enum_face(choices):
    """_EnumDialFace with `_choices` set directly, skipping __init__ (and
    therefore any real Qt widget construction) -- see the note above."""
    face = _EnumDialFace.__new__(_EnumDialFace)
    face._choices = list(choices)
    return face


def test_enum_dial_face_angle_for_spreads_choices_across_the_span():
    face = _bare_enum_face(["OFF", "ON", "AUTO"])
    assert face._angle_for(0) == pytest.approx(225.0)
    assert face._angle_for(2) == pytest.approx(225.0 - 270.0)
    assert face._angle_for(1) == pytest.approx(225.0 - 135.0)


def test_enum_dial_face_angle_for_single_choice_is_centered():
    face = _bare_enum_face(["ONLY"])
    assert face._angle_for(0) == pytest.approx(225.0 - 135.0)


def test_nearest_enum_position_picks_the_closest_angle():
    # OFF(225) / ON(90) / AUTO(-45) -- a click near 90 degrees should
    # resolve to "ON" (index 1), same layout _EnumDialFace._angle_for
    # produces for 3 choices
    angles = [225.0, 90.0, -45.0]
    assert _nearest_enum_position(100.0, angles) == 1
    assert _nearest_enum_position(225.0, angles) == 0
    assert _nearest_enum_position(-40.0, angles) == 2


def test_nearest_enum_position_wraps_across_the_360_degree_seam():
    angles = [225.0, 90.0, -45.0]
    # 315 degrees is the same direction as -45 degrees (AUTO), the long
    # way around -- exercises the wraparound math rather than a plain
    # subtraction, which would see a huge, wrong distance here
    assert _nearest_enum_position(315.0 + 5.0, angles) == 2


def test_nearest_enum_position_empty_angles_returns_none():
    assert _nearest_enum_position(90.0, []) is None


def test_dial_switches_to_enum_face_for_enum_item():
    app = _app()
    item = _FakeEnumAdjustable("mode1", ["OFF", "ON", "AUTO"], value=0)
    w = Dial(item=item, title="Mode", poll_interval=0.02)
    try:
        assert w.enum_strs == ["OFF", "ON", "AUTO"]
        assert w._stack.currentWidget() is w._enum_face
        assert w._enum_face._interactive is True
        assert w._enum_face._choices == ["OFF", "ON", "AUTO"]
    finally:
        w.stop()


def test_dial_enum_render_updates_current_position():
    app = _app()
    item = _FakeEnumAdjustable("mode1", ["OFF", "ON", "AUTO"], value=0)
    w = Dial(item=item, title="Mode", poll_interval=0.02)
    try:
        w._render(2)
        assert w._enum_face._current == 2
        assert w._value_label.text() == "AUTO"
    finally:
        w.stop()


def test_dial_enum_choice_click_writes_target_value():
    app = _app()
    item = _FakeEnumAdjustable("mode1", ["OFF", "ON", "AUTO"], value=0)
    w = Dial(item=item, title="Mode", poll_interval=0.02)
    try:
        w._on_enum_chosen(2)
        assert _pump(app, lambda: item.set_calls == ["AUTO"])
    finally:
        w.stop()


def test_dial_enum_readonly_item_is_not_interactive():
    app = _app()

    class _FakeEnumDetector(_FakeDetector):
        def __init__(self, name, enum_strs, value=0):
            super().__init__(name, value)
            self.enum_strs = list(enum_strs)

    item = _FakeEnumDetector("mode1", ["OFF", "ON"], value=0)
    w = Dial(item=item, title="Mode", poll_interval=0.02)
    try:
        assert w.settable is False
        assert w._enum_face._interactive is False
    finally:
        w.stop()


def test_dial_plain_numeric_item_still_uses_numeric_page():
    app = _app()
    item = _FakeAdjustable("m1", value=5.0, low_limit=0, high_limit=10)
    w = Dial(item=item, title="M1", vmin=0.0, vmax=10.0, poll_interval=0.02)
    try:
        assert w.enum_strs is None
        assert w._stack.currentWidget() is w._numeric_page
    finally:
        w.stop()


def test_dial_reattachment_switches_between_numeric_and_enum():
    app = _app()
    numeric_item = _FakeAdjustable("m1", value=5.0)
    enum_item = _FakeEnumAdjustable("mode1", ["OFF", "ON"], value=1)
    w = Dial(item=numeric_item, title="M1", vmin=0.0, vmax=10.0, poll_interval=0.02)
    try:
        assert w._stack.currentWidget() is w._numeric_page
        w.set_item(enum_item, title="Mode")
        assert w._stack.currentWidget() is w._enum_face
        assert w.enum_strs == ["OFF", "ON"]
        w.set_item(numeric_item, title="M1")
        assert w._stack.currentWidget() is w._numeric_page
        assert w.enum_strs is None
    finally:
        w.stop()


def test_slider_settable_writes_on_release():
    app = _app()
    item = _FakeAdjustable("m1", value=0.0)
    w = Slider(item=item, title="M1", vmin=0.0, vmax=10.0, step=1.0, poll_interval=0.02)
    try:
        w._slider.setValue(7)
        w._on_release()
        assert item.set_calls == [7.0]
    finally:
        w.stop()


def test_gadget_stop_halts_polling():
    app = _app()
    item = _FakeDetector("d1", value=1.0)
    w = NumericTile(item=item, title="D1", poll_interval=0.02)
    _pump(app, lambda: len(w._values) >= 1)
    w.stop()
    # one more poll can land right after stop() (the background thread may
    # already be mid-cycle when the stop event is set) -- a benign race,
    # not a bug; give it a moment to settle before taking the "before" count
    time.sleep(0.05)
    app.processEvents()
    n_before = len(w._values)
    time.sleep(0.15)
    app.processEvents()
    assert len(w._values) == n_before  # no further polling once truly stopped


# -- context menu wiring --


def test_attach_indicator_menu_sets_custom_context_menu_policy():
    app = _app()
    label = QtWidgets.QLabel("m1")
    item = _FakeDetector("m1", value=1.0)
    attach_indicator_menu(label, item, "m1")
    assert label.contextMenuPolicy() == QtCore.Qt.CustomContextMenu


# -- Qt Designer promotability: parent-only construction + set_item --


def test_gadget_constructs_with_only_parent_like_qt_designer_would():
    # Designer always instantiates a promoted widget as ClassName(parent)
    # -- one positional arg -- so this must not raise and must render inert
    app = _app()
    for _label, factory, _needs_range in INDICATOR_TYPES:
        w = factory(None)
        try:
            assert w.item is None
            assert w.windowTitle() == "(unattached)"
        finally:
            w.stop()


def test_set_item_attaches_and_starts_polling_on_a_designer_style_placeholder():
    app = _app()
    w = NumericTile(None)
    try:
        assert w.item is None
        item = _FakeDetector("d1", value=42.0)
        w.set_item(item, title="D1")
        assert w.windowTitle() == "D1"
        assert _pump(app, lambda: w._number_label.text() == "42")
    finally:
        w.stop()


def test_set_item_reattachment_updates_dial_settability():
    app = _app()
    w = Dial(None, vmin=0.0, vmax=10.0, step=1.0)
    try:
        assert w.settable is False
        assert not w._dial.isEnabled()

        adj = _FakeAdjustable("m1", value=0.0)
        w.set_item(adj, title="M1")
        assert _pump(app, lambda: w.settable is True)
        assert w._dial.isEnabled()

        w._dial.setValue(3)
        w._on_release()
        assert adj.set_calls == [3.0]
    finally:
        w.stop()


def test_set_item_stops_previous_polling_of_the_old_item():
    app = _app()
    item1 = _FakeDetector("d1", value=1.0)
    w = NumericTile(item=item1, poll_interval=0.02)
    try:
        assert _pump(app, lambda: len(w._values) >= 1)
        item2 = _FakeDetector("d2", value=99.0)
        w.set_item(item2, title="D2")
        assert _pump(app, lambda: w._number_label.text() == "99")
        # only item2 should be polled from here on
        n = len(w._values)
        time.sleep(0.1)
        app.processEvents()
        assert w._number_label.text() == "99"
        assert len(w._values) >= n
    finally:
        w.stop()


# -- item_path / resolve_item_path round-trip --


def test_item_path_uses_alias_full_name_when_present():
    class FakeAlias:
        def get_full_name(self):
            return "bernina.cam_west.intensity"

    item = _FakeDetector("intensity")
    item.alias = FakeAlias()
    assert item_path(item) == "bernina.cam_west.intensity"


def test_item_path_falls_back_when_no_alias():
    item = _FakeDetector("intensity")
    assert item_path(item, fallback="intensity") == "intensity"


def test_resolve_item_path_walks_attributes():
    class Leaf:
        pass

    leaf = Leaf()

    class Namespace:
        pass

    ns = Namespace()
    child = Namespace()
    child.intensity = leaf
    ns.cam_west = child

    assert resolve_item_path(ns, "cam_west.intensity") is leaf


def test_resolve_item_path_returns_none_for_missing_segment():
    class Namespace:
        pass

    ns = Namespace()
    assert resolve_item_path(ns, "no_such.path") is None


def test_resolve_item_path_empty_path_returns_none():
    assert resolve_item_path(object(), "") is None


def test_sanitize_object_name_is_stable_and_qt_safe():
    name = _sanitize_object_name("bernina.cam_west.intensity")
    assert name == "eco_bernina_cam_west_intensity"
    assert _sanitize_object_name("bernina.cam_west.intensity") == name  # deterministic


def test_create_and_add_sets_object_and_accessible_name():
    from eco.widgets.indicator_widgets import _create_and_add

    class FakeDashboard:
        def __init__(self):
            self.added = []

        def add_widget(self, widget, title=None):
            self.added.append((widget, title))

    app = _app()

    class FakeAlias:
        def get_full_name(self):
            return "bernina.shutter"

    item = _FakeDetector("shutter", value=True)
    item.alias = FakeAlias()

    dashboard = FakeDashboard()
    _create_and_add(item, "shutter", "LED", dashboard)

    assert len(dashboard.added) == 1
    widget, title = dashboard.added[0]
    try:
        assert title == "shutter"
        assert widget.accessibleName() == "bernina.shutter"
        assert widget.objectName() == "eco_bernina_shutter"
        assert widget.indicator_kind == "LED"
    finally:
        widget.stop()



# -- display range: right-click "Set range..." ------------------------------


def test_guess_range_skips_unset_motor_limits():
    # a motor record's +-1e10 soft limits: the gauge would sit at the
    # middle forever -- fall through to the current value +-10 %
    item = _FakeAdjustable("hgap", value=2.0, low_limit=-1e10, high_limit=1e10)
    assert _guess_range(item) == pytest.approx((1.8, 2.2))


def test_guess_range_skips_unset_pair_and_takes_the_next_candidate():
    item = _FakeAdjustable("m", value=5.0, low_limit=-1e10, high_limit=1e10)
    item.llm, item.hlm = -3.0, 7.0
    assert _guess_range(item) == (-3.0, 7.0)


def test_guess_range_equal_limits_and_zero_value_fall_back_to_default():
    item = _FakeAdjustable("m", value=0.0, low_limit=1.0, high_limit=1.0)
    assert _guess_range(item) == (0.0, 100.0)


def test_guess_range_uses_given_current_value_without_reading():
    class Unreadable(_FakeDetector):
        def get_current_value(self):
            raise AssertionError("must not read")

    assert _guess_range(Unreadable("d"), current=-10.0) == pytest.approx((-11.0, -9.0))


def test_range_around():
    assert range_around(10.0) == pytest.approx((9.0, 11.0))
    assert range_around(0.0) is None
    assert range_around("abc") is None


def test_range_dialog_accepts_scientific_notation_and_rejects_max_below_min():
    app = _app()  # keep a reference, or it is collected at once
    dialog = RangeDialog(0.0, 1.0)
    ok = dialog.buttons.button(QtWidgets.QDialogButtonBox.Ok)
    assert dialog.min_edit.text() == "0" and dialog.max_edit.text() == "1"
    dialog.min_edit.setText("1e-6")
    dialog.max_edit.setText("3.2e4")
    assert dialog.values() == (1e-6, 3.2e4) and ok.isEnabled()
    dialog.max_edit.setText("-5")
    assert dialog.values() is None and not ok.isEnabled()
    dialog.max_edit.setText("1e-6")  # equal is not a range either
    assert dialog.values() is None and not ok.isEnabled()


def test_bar_gauge_set_range_rerenders_and_updates_export_kwargs():
    app = _app()
    item = _FakeDetector("d1", value=25.0)
    w = BarGauge(item=item, title="D1", vmin=0.0, vmax=50.0, poll_interval=0.02)
    w.export_kwargs = {"vmin": 0.0, "vmax": 50.0}
    try:
        assert _pump(app, lambda: w._bar.value() == 500)
        w.stop()  # no new polls: the change below must come from set_range itself
        app.processEvents()
        w.set_range(20.0, 30.0)
        assert (w.vmin, w.vmax) == (20.0, 30.0)
        assert w.export_kwargs == {"vmin": 20.0, "vmax": 30.0}
        assert w._bar.value() == 500  # 25 in 20..30
        w.set_range(0.0, 100.0)
        assert w._bar.value() == 250
        with pytest.raises(ValueError):
            w.set_range(5.0, 5.0)
        assert (w.vmin, w.vmax) == (0.0, 100.0)
    finally:
        w.stop()


def test_analog_gauge_range_around_current_and_reset():
    app = _app()
    item = _FakeAdjustable("m1", value=50.0, low_limit=0, high_limit=200)
    w = AnalogGauge(item=item, title="M1", vmin=0.0, vmax=200.0, poll_interval=0.02)
    try:
        assert _pump(app, lambda: w._value == 50.0)
        assert w.range_around_current() is True
        assert (w.vmin, w.vmax) == pytest.approx((45.0, 55.0))
        assert w.export_kwargs["vmin"] == pytest.approx(45.0)
        w.reset_range()
        assert (w.vmin, w.vmax) == (0.0, 200.0)
    finally:
        w.stop()


def _menu_texts(w):
    menu = w.range_menu()
    return None if menu is None else [a.text() for a in menu.actions()]


def test_range_menu_only_on_range_gadgets():
    app = _app()  # keep a reference, or it is collected at once
    item = _FakeDetector("d1", value=1.0)
    with_menu = [
        BarGauge(item=item, title="b", poll_interval=10),
        AnalogGauge(item=item, title="a", poll_interval=10),
        Dial(item=item, title="d", poll_interval=10),
        Slider(item=item, title="s", poll_interval=10),
    ]
    without = [
        LEDIndicator(item=item, title="l", poll_interval=10),
        StripChart(item=item, title="c", poll_interval=10),
        NumericTile(item=item, title="n", poll_interval=10),
    ]
    enum_dial = Dial(item=_FakeEnumAdjustable("mode", ["A", "B"]), title="e", poll_interval=10)
    try:
        for w in with_menu:
            assert w.contextMenuPolicy() == QtCore.Qt.CustomContextMenu
            assert _menu_texts(w) == [
                "Set range...",
                "Range around current value",
                "Reset to item limits",
            ]
        for w in without:
            assert _menu_texts(w) is None
            assert w.contextMenuPolicy() != QtCore.Qt.CustomContextMenu
        assert _menu_texts(enum_dial) is None
    finally:
        for w in with_menu + without + [enum_dial]:
            w.stop()


@pytest.mark.parametrize("cls, attr", [(Dial, "_dial"), (Slider, "_slider")])
def test_dial_and_slider_rerange_without_writing(cls, attr):
    app = _app()
    item = _FakeAdjustable("m1", value=5.0)
    w = cls(item=item, title="M1", vmin=0.0, vmax=10.0, poll_interval=0.02)
    control = getattr(w, attr)
    try:
        assert w.settable
        assert _pump(app, lambda: control.value() == 100)  # 5 of 0..10 in 200 steps
        w.stop()
        app.processEvents()
        w.set_range(4.0, 6.0)
        assert w.step == pytest.approx(0.01)  # re-derived: (6-4)/200
        assert control.maximum() == 200
        assert control.value() == 100  # 5 in 4..6
        assert w.export_kwargs == {"vmin": 4.0, "vmax": 6.0}
        w.range_around_current()
        w.reset_range()
        app.processEvents()
        assert item.set_calls == []
    finally:
        w.stop()


def test_explicit_step_is_kept_on_rerange():
    app = _app()  # keep a reference, or it is collected at once
    item = _FakeAdjustable("m1", value=5.0)
    w = Slider(item=item, title="M1", vmin=0.0, vmax=10.0, step=0.5, poll_interval=10)
    try:
        w.set_range(0.0, 20.0)
        assert w.step == 0.5 and w._slider.maximum() == 40
    finally:
        w.stop()


def test_create_indicator_range_survives_in_export_kwargs():
    app = _app()  # keep a reference, or it is collected at once
    item = _FakeAdjustable("hgap", value=2.0, low_limit=-1e10, high_limit=1e10)
    w = create_indicator("Bar gauge", item, title="hgap")
    try:
        assert w.export_kwargs == pytest.approx({"vmin": 1.8, "vmax": 2.2})
        w.set_range(0.0, 10.0)
        rebuilt = create_indicator("Bar gauge", item, title="hgap", **w.export_kwargs)
        try:
            assert (rebuilt.vmin, rebuilt.vmax) == (0.0, 10.0)
        finally:
            rebuilt.stop()
    finally:
        w.stop()
