"""Focused coverage for eco.widgets.widget_tray's Log Viewer button (see
eco.widgets.jupyter_sidecar's equivalent for the JupyterLab counterpart) --
the rest of make_namespace_dashboard/NamespaceLauncherWidget is exercised
via scripted manual testing (constructing them needs a real ipywidgets
display context to be fully meaningful; see this session's own testing
notes), not a dedicated suite here yet.
"""
from eco.widgets.widget_tray import make_namespace_dashboard


class _FakeItem:
    def widget(self):
        import ipywidgets as widgets

        return widgets.HTML("fake widget")


class _FakeNamespace:
    def __init__(self):
        self.initialized_names = {"cam_west"}
        self.lazy_names = set()
        self.failed_names = set()
        self._items = {"cam_west": _FakeItem()}
        self._required = set()

    def resolve_item(self, name):
        return self._items.get(name)

    def required_names(self, value=None):
        if value is None:
            return sorted(self._required)
        self._required = set(value)


def test_log_viewer_button_calls_eco_logs_widget_prefer_jupyter(monkeypatch):
    """Must go through eco.logs.widget(prefer="jupyter") -- not a parallel,
    duplicated HTML-rendering path -- so it stays consistent with every
    other logs.widget() caller (eco desktop's Tools menu,
    eco.widgets.jupyter_sidecar's JupyterLab button)."""
    import eco.logs

    calls = []
    monkeypatch.setattr(eco.logs, "widget", lambda prefer: calls.append(prefer))

    dash = make_namespace_dashboard(_FakeNamespace())
    header, _tray_box = dash.children
    _launcher, _status, btn_row, log_output = header.children
    _layout_toggle, log_btn = btn_row.children
    # A real ipykernel-backed InteractiveShell.instance() left behind by an
    # earlier, unrelated test in this same pytest process (a real,
    # process-wide IPython singleton) crashes Output.clear_output()'s
    # attempt to actually send a clear through it -- irrelevant to what
    # this test verifies (that clicking the button calls eco.logs.widget),
    # so short-circuit it.
    monkeypatch.setattr(log_output, "clear_output", lambda *a, **kw: None)

    assert log_btn.description == "Log Viewer"
    log_btn.click()

    assert calls == ["jupyter"]
