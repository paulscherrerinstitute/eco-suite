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


def test_default_dock_in_is_none_by_default():
    assert Assembly._default_dock_in is None


def test_widget_forwards_kwargs_to_default_widget_override():
    """Regression test: widget() used to call a _default_widget override
    with zero arguments, silently dropping any **kwargs passed to
    widget() itself (e.g. camera.widget(rate_hz=25.0))."""
    calls = []

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self, **kwargs):
            calls.append(kwargs)
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    obj.widget(rate_hz=25.0, theme="dark")

    assert calls == [{"rate_hz": 25.0, "theme": "dark"}]


class _FakeContainer:
    def __init__(self):
        self.calls = []

    def host_widget(self, name, widget_obj):
        self.calls.append((name, widget_obj))


def test_widget_dock_in_true_resolves_via_get_or_create_default_container(monkeypatch):
    fake_container = _FakeContainer()
    monkeypatch.setattr(
        "eco.widgets.desktop_app.get_or_create_default_container",
        lambda: fake_container,
        raising=False,
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    result = obj.widget(dock_in=True)

    assert result == "the viewer object"
    assert fake_container.calls == [(obj.alias.get_full_name(), "the viewer object")]


def test_widget_dock_in_string_auto_also_resolves_via_registry(monkeypatch):
    fake_container = _FakeContainer()
    monkeypatch.setattr(
        "eco.widgets.desktop_app.get_or_create_default_container",
        lambda: fake_container,
        raising=False,
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    obj.widget(dock_in="auto")

    assert fake_container.calls == [(obj.alias.get_full_name(), "the viewer object")]


def test_widget_dock_in_explicit_instance_used_as_is(monkeypatch):
    def _boom():
        raise AssertionError("get_or_create_default_container should not be called")

    monkeypatch.setattr(
        "eco.widgets.desktop_app.get_or_create_default_container", _boom, raising=False
    )
    fake_container = _FakeContainer()

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    obj.widget(dock_in=fake_container)

    assert fake_container.calls == [(obj.alias.get_full_name(), "the viewer object")]


def test_widget_dock_in_none_forces_standalone_even_with_default_dock_in_true(monkeypatch):
    def _boom():
        raise AssertionError("get_or_create_default_container should not be called")

    monkeypatch.setattr(
        "eco.widgets.desktop_app.get_or_create_default_container", _boom, raising=False
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"
        _default_dock_in = True

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    result = obj.widget(dock_in=None)

    assert result == "the viewer object"


def test_widget_dock_in_omitted_uses_class_default_false(monkeypatch):
    def _boom():
        raise AssertionError("get_or_create_default_container should not be called")

    monkeypatch.setattr(
        "eco.widgets.desktop_app.get_or_create_default_container", _boom, raising=False
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    obj.widget()  # dock_in omitted entirely -- _default_dock_in stays None


def test_widget_dock_in_omitted_uses_class_default_true(monkeypatch):
    fake_container = _FakeContainer()
    monkeypatch.setattr(
        "eco.widgets.desktop_app.get_or_create_default_container",
        lambda: fake_container,
        raising=False,
    )

    class FakeCameraLike(Assembly):
        _default_widget = "_widget_viewer"
        _default_dock_in = True

        def _widget_viewer(self):
            return "the viewer object"

    obj = FakeCameraLike(name="fake_cam")
    obj.widget()

    assert fake_container.calls == [(obj.alias.get_full_name(), "the viewer object")]
