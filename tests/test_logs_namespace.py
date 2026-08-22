import pytest

import eco.logs as logs
from eco.logs import _in_jupyter, _kernel_record_to_entry, _snippet_to_entry
from eco.widgets import kernel_registry


# -- _kernel_record_to_entry --------------------------------------------------


def test_kernel_record_input():
    e = _kernel_record_to_entry({"t": 1.0, "event": "input", "code": "mono.mv(1)"}, "sess")
    assert e.kind == "input"
    assert e.text == "mono.mv(1)"
    assert e.is_error is False


def test_kernel_record_widget_control():
    """Same code-is-the-text contract as "input" (see
    KernelSession.log_widget_control's docstring) -- both kinds must be
    directly usable as script lines in log_timeline_qt's "copy selected
    as script"."""
    e = _kernel_record_to_entry(
        {"t": 1.0, "event": "widget_control", "code": "cam_west.widget()"}, "sess"
    )
    assert e.kind == "widget_control"
    assert e.text == "cam_west.widget()"
    assert e.is_error is False


def test_kernel_record_error_sets_is_error():
    e = _kernel_record_to_entry(
        {"t": 1.0, "event": "error", "ename": "ValueError", "evalue": "bad"}, "sess"
    )
    assert e.kind == "error"
    assert e.text == "ValueError: bad"
    assert e.is_error is True


def test_kernel_record_result_and_stream():
    r = _kernel_record_to_entry({"t": 1.0, "event": "result", "text": "Out[]: 1"}, "sess")
    assert r.kind == "result" and r.text == "Out[]: 1"
    s = _kernel_record_to_entry(
        {"t": 1.0, "event": "stream", "name": "stdout", "text": "hi\n"}, "sess"
    )
    assert s.kind == "stream" and s.text == "[stdout] hi"


def test_kernel_record_session_start_uses_label_or_falls_back():
    e = _kernel_record_to_entry(
        {"t": 1.0, "event": "session_start", "kind": "console", "label": "bernina", "pid": 5},
        "sess",
    )
    assert e.kind == "session"
    assert "console:bernina" in e.text
    assert "5" in e.text

    # real session_end records (see kernel_registry.stop_kernel) carry no
    # kind/label -- the file-stem fallback is what makes them identifiable
    e2 = _kernel_record_to_entry({"t": 1.0, "event": "session_end"}, "sess")
    assert "sess" in e2.text


def test_kernel_record_missing_t_or_event_returns_none():
    assert _kernel_record_to_entry({"event": "input", "code": "x"}, "sess") is None
    assert _kernel_record_to_entry({"t": 1.0}, "sess") is None


def test_kernel_record_unknown_event_falls_back_to_kind_equals_event():
    e = _kernel_record_to_entry({"t": 1.0, "event": "custom_thing", "foo": "bar"}, "sess")
    assert e.kind == "custom_thing"
    assert "foo" in e.text and "bar" in e.text


# -- _snippet_to_entry --------------------------------------------------------


class _FakeSnippet:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_snippet_to_entry_parses_iso_timestamp_and_fields():
    snip = _FakeSnippet(
        id="abc",
        createdAt="2026-08-19T09:05:00.000Z",
        createdBy="bob",
        tags=["mono"],
        snippetType="paragraph",
    )
    e = _snippet_to_entry(snip)
    assert e.ref == "abc"
    assert e.tags == ("mono",)
    assert e.kind == "paragraph"
    assert "bob" in e.text
    assert e.t > 0


def test_snippet_to_entry_missing_created_at_returns_none():
    assert _snippet_to_entry(_FakeSnippet(id="abc")) is None


def test_snippet_to_entry_bad_timestamp_returns_none():
    assert _snippet_to_entry(_FakeSnippet(id="abc", createdAt="not-a-date")) is None


# -- _in_jupyter ---------------------------------------------------------------


def test_in_jupyter_false_when_no_ipython(monkeypatch):
    import IPython

    monkeypatch.setattr(IPython, "get_ipython", lambda: None)
    assert _in_jupyter() is False


def test_in_jupyter_true_for_zmq_shell(monkeypatch):
    import IPython

    class ZMQInteractiveShell:
        pass

    monkeypatch.setattr(IPython, "get_ipython", lambda: ZMQInteractiveShell())
    assert _in_jupyter() is True


def test_in_jupyter_false_for_terminal_shell(monkeypatch):
    import IPython

    class TerminalInteractiveShell:
        pass

    monkeypatch.setattr(IPython, "get_ipython", lambda: TerminalInteractiveShell())
    assert _in_jupyter() is False


# -- kernel log side, via real kernel_registry --------------------------------


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(kernel_registry, "_registry", [])


def test_kernel_entries_reads_and_sorts_across_sessions(tmp_path):
    s1 = kernel_registry.KernelSession(kind="console", label="a", log_dir=tmp_path)
    s1.log_input("first")
    s2 = kernel_registry.KernelSession(kind="console", label="b", log_dir=tmp_path)
    s2.log_input("second")

    entries = logs._kernel_entries(log_dir=tmp_path)
    inputs = [(e.kind, e.text) for e in entries if e.kind == "input"]
    assert inputs == [("input", "first"), ("input", "second")]
    assert entries == sorted(entries, key=lambda e: e.t)


def test_kernel_entries_single_session_only(tmp_path):
    s1 = kernel_registry.KernelSession(kind="console", label="a", log_dir=tmp_path)
    s1.log_input("only this one")
    kernel_registry.KernelSession(kind="console", label="b", log_dir=tmp_path).log_input(
        "not this one"
    )

    entries = logs._kernel_entries(session=s1.log_path)
    texts = [e.text for e in entries if e.kind == "input"]
    assert texts == ["only this one"]


def test_tail_returns_last_n(tmp_path):
    s1 = kernel_registry.KernelSession(kind="console", log_dir=tmp_path)
    for i in range(5):
        s1.log_input(f"cmd {i}")

    result = logs.tail(n=2, session=s1.log_path)
    assert [e.text for e in result] == ["cmd 3", "cmd 4"]


def test_sessions_returns_a_list_without_raising():
    assert isinstance(logs.sessions(), list)


# -- scilog side, via a fake client (no network) -------------------------------


class _FakeSciLogClient:
    def __init__(self, snippets):
        self._snippets = snippets
        self.calls = []

    def get_snippets(self, where=None, fields=None, order=None, limit=0, **kw):
        self.calls.append({"where": where, "fields": fields})
        if where and "id" in where:
            return [s for s in self._snippets if s.id == where["id"]]
        return self._snippets


def test_scilog_only_fetches_cheap_fields_not_bodies(monkeypatch):
    snippets = [
        _FakeSnippet(
            id="s1",
            createdAt="2026-08-19T09:00:00.000Z",
            createdBy="alice",
            tags=["mono"],
            snippetType="paragraph",
            textcontent="<p>secret body</p>",
        ),
    ]
    client = _FakeSciLogClient(snippets)
    monkeypatch.setattr(logs, "_scilog_client", lambda **kw: client)
    monkeypatch.setattr(logs, "_open_timeline", lambda entries, **kw: entries)

    entries = logs.scilog(pgroup="p20240", prefer="html")
    assert len(entries) == 1
    assert entries[0].tags == ("mono",)
    assert entries[0].ref == "s1"
    assert "secret body" not in entries[0].text  # never fetched for the list

    fields = client.calls[0]["fields"]
    assert set(fields) == {"id", "createdAt", "tags", "snippetType", "createdBy"}
    assert "textcontent" not in fields


def test_open_scilog_does_the_one_expensive_fetch(monkeypatch):
    snippets = [
        _FakeSnippet(id="s1", createdAt="2026-08-19T09:00:00.000Z", createdBy="alice", textcontent="<p>full body</p>"),
        _FakeSnippet(id="s2", createdAt="2026-08-19T09:05:00.000Z", createdBy="bob", textcontent="<p>other</p>"),
    ]
    client = _FakeSciLogClient(snippets)
    monkeypatch.setattr(logs, "_scilog_client_cache", client)

    html = logs.open_scilog("s1")
    assert "full body" in html
    assert "other" not in html
    assert client.calls[-1]["where"] == {"id": "s1"}


def test_open_scilog_missing_id_reports_not_found(monkeypatch):
    monkeypatch.setattr(logs, "_scilog_client_cache", _FakeSciLogClient([]))
    assert "not found" in logs.open_scilog("nope")


# -- _open_timeline dispatch (never constructs a real Qt widget) --------------


def test_open_timeline_prefers_html_in_jupyter(monkeypatch):
    calls = []
    monkeypatch.setattr(logs, "_in_jupyter", lambda: True)
    monkeypatch.setattr(
        "eco.widgets.log_timeline_html.show", lambda entries, **kw: calls.append(("html", kw))
    )

    result = logs._open_timeline([], title="t", subtitle="s", prefer="auto")
    assert result is None
    assert calls and calls[0][0] == "html"


def test_open_timeline_prefers_qt_outside_jupyter(monkeypatch):
    calls = []
    monkeypatch.setattr(logs, "_in_jupyter", lambda: False)
    monkeypatch.setattr(
        "eco.widgets.log_timeline_qt.make_log_timeline_qt_window",
        lambda entries, **kw: calls.append(("qt", kw)) or "handle",
    )

    result = logs._open_timeline([], title="t", subtitle="s", prefer="auto")
    assert result == "handle"
    assert calls[0][0] == "qt"


def test_open_timeline_falls_back_to_webapp_when_qt_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(logs, "_in_jupyter", lambda: False)

    def _boom(*a, **kw):
        raise RuntimeError("no display")

    monkeypatch.setattr("eco.widgets.log_timeline_qt.make_log_timeline_qt_window", _boom)
    target = tmp_path / "out.html"
    monkeypatch.setattr("eco.widgets.log_timeline_html.write_html_file", lambda entries, **kw: target)

    result = logs._open_timeline([], title="t", subtitle="s", prefer="auto")
    assert result == target


def test_open_timeline_qt_forced_raises_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(logs, "_in_jupyter", lambda: False)

    def _boom(*a, **kw):
        raise RuntimeError("no display")

    monkeypatch.setattr("eco.widgets.log_timeline_qt.make_log_timeline_qt_window", _boom)

    with pytest.raises(RuntimeError):
        logs._open_timeline([], title="t", subtitle="s", prefer="qt")


def test_open_timeline_sidecar_renders_html_into_a_sidecar_panel(monkeypatch):
    """JupyterLab's Log Viewer button (eco.widgets.jupyter_sidecar.
    NamespaceDashboard) goes through prefer="sidecar" -- must render the
    same HTML the html/jupyter path would, into a real dockable panel
    (eco.widgets.jupyter_sidecar.open_html_in_sidecar), not some
    parallel, duplicated rendering path."""
    calls = []
    monkeypatch.setattr(
        "eco.widgets.log_timeline_html.render_html",
        lambda entries, **kw: "<b>rendered</b>",
    )
    monkeypatch.setattr(
        "eco.widgets.jupyter_sidecar.open_html_in_sidecar",
        lambda html, **kw: calls.append((html, kw)) or "sidecar-handle",
    )

    result = logs._open_timeline([], title="t", subtitle="s", prefer="sidecar")

    assert result == "sidecar-handle"
    assert calls == [("<b>rendered</b>", {"title": "t"})]


def test_open_timeline_rejects_unknown_prefer():
    with pytest.raises(ValueError):
        logs._open_timeline([], title="t", subtitle="s", prefer="bogus")
