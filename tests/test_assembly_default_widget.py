from eco.elements.assembly import Assembly


def test_widget_uses_default_widget_override_when_set():
    calls = []

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            calls.append("viewer")
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    result = obj.widget()

    assert result == "the viewer object"
    assert calls == ["viewer"]


def test_widget_falls_back_to_generic_dispatch_when_no_override(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eco.utilities.utilities.is_notebook", lambda: True, raising=False
    )
    monkeypatch.setattr(
        "eco.widgets.display_widget.make_assembly_widget",
        lambda obj, show_hidden=False: calls.append((obj, show_hidden)) or "generic widget",
        raising=False,
    )

    plain = Assembly(name="plain")
    result = plain.widget()

    assert result == "generic widget"
    assert calls == [(plain, False)]


def test_widget_override_name_pointing_nowhere_falls_back(monkeypatch):
    # _default_widget naming a method that doesn't exist shouldn't crash --
    # falls back to the generic dispatch instead
    monkeypatch.setattr(
        "eco.utilities.utilities.is_notebook", lambda: True, raising=False
    )
    monkeypatch.setattr(
        "eco.widgets.display_widget.make_assembly_widget",
        lambda obj, show_hidden=False: "generic widget",
        raising=False,
    )

    class BrokenOverride(Assembly):
        _default_widget = "no_such_method"

    obj = BrokenOverride(name="broken")
    assert obj.widget() == "generic widget"


def test_default_widget_is_none_by_default():
    assert Assembly._default_widget is None


def test_widget_assembly_bypasses_the_override(monkeypatch):
    """_widget_assembly() is the generic property-grid widget directly --
    widget() itself is just a thin dispatcher on top of it (checks
    _default_widget, else calls _widget_assembly()). Calling
    _widget_assembly() straight always gets the plain grid regardless of
    what widget() itself would dispatch to, which is what
    eco.widgets.containers.assembly_widget() relies on. Same mocking
    rationale as test_widget_normal_true_skips_the_override (avoid
    blocking in a real Qt event loop)."""
    calls = []
    monkeypatch.setattr(
        "eco.utilities.utilities.is_notebook", lambda: True, raising=False
    )
    monkeypatch.setattr(
        "eco.widgets.display_widget.make_assembly_widget",
        lambda obj, show_hidden=False: "generic widget",
        raising=False,
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            calls.append("viewer")
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    result = obj._widget_assembly()

    assert calls == []  # override never called
    assert result == "generic widget"


def test_widget_normal_true_skips_the_override(monkeypatch):
    """Regression test for a real bug: AxisPTZStreamQt's own "Settings"
    button calls cam.widget() wanting the plain property grid, but with
    _default_widget = "_widget_viewer" set, plain .widget() just reopened the same
    viewer the button was clicked from. normal=True is the escape hatch.

    Mocks the generic dispatch target (same as
    test_widget_falls_back_to_generic_dispatch_when_no_override) --
    without it, normal=True's fallthrough reaches the real
    make_assembly_qt_window, which defaults to auto_start=True and blocks
    in a real Qt event loop (DisplayQt.start() -> run() -> app.exec_())
    since nothing in this test would ever close that window."""
    calls = []
    monkeypatch.setattr(
        "eco.utilities.utilities.is_notebook", lambda: True, raising=False
    )
    monkeypatch.setattr(
        "eco.widgets.display_widget.make_assembly_widget",
        lambda obj, show_hidden=False: "generic widget",
        raising=False,
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            calls.append("viewer")
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    obj.widget()  # normal default (False): uses the override
    assert calls == ["viewer"]

    result = obj.widget(normal=True)
    assert calls == ["viewer"]  # override not called a second time
    assert result == "generic widget"


def test_widget_normal_true_still_dispatches_generically(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eco.utilities.utilities.is_notebook", lambda: True, raising=False
    )
    monkeypatch.setattr(
        "eco.widgets.display_widget.make_assembly_widget",
        lambda obj, show_hidden=False: calls.append((obj, show_hidden)) or "generic widget",
        raising=False,
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    result = obj.widget(normal=True)

    assert result == "generic widget"
    assert calls == [(obj, False)]
