"""eco.utilities.tips (the "Did you know?" tips) and eco.widgets.tip_qt."""
import pytest

from eco.utilities import tips


@pytest.fixture
def tip_dir(tmp_path, monkeypatch):
    for i, (title, details) in enumerate(
        [("First tip", "scanning"), ("Second tip", ""), ("Third tip", "examples/x")]
    ):
        (tmp_path / f"2026-0{i + 1}-01-tip{i}.md").write_text(
            "---\n"
            f"added: 2026-0{i + 1}-01\n"
            "tags: [a, b]\n"
            f"details: {details}\n"
            "---\n\n"
            f"# {title}\n\n"
            "Some **bold** text\nover two lines.\n\n"
            "```python\nmot.tweak(0.1)\n```\n\n"
            "More: {doc}`../scanning`\n"
        )
    (tmp_path / "index.md").write_text("# not a tip\n")
    monkeypatch.setattr(tips, "TIPS_DIR", tmp_path)
    monkeypatch.setattr(tips, "history", tips.TipHistory())
    return tmp_path


def test_parse_tip(tip_dir):
    loaded = tips.load_tips()
    assert [t.title for t in loaded] == ["First tip", "Second tip", "Third tip"]
    first = loaded[0]
    assert first.added == "2026-01-01"
    assert first.tags == ["a", "b"]
    assert first.details == "scanning"
    assert first.body.startswith("Some **bold** text")
    assert loaded[1].details == "" and loaded[1].details_url is None


def test_doc_links_point_at_docs_site(tip_dir, monkeypatch):
    monkeypatch.setattr(tips, "DOCS_URL", "http://host:8095/")
    first = tips.load_tips()[0]
    assert first.details_url == "http://host:8095/scanning.html"
    assert first.url == "http://host:8095/did_you_know/2026-01-01-tip0.html"
    assert "[scanning](http://host:8095/scanning.html)" in tips.markdown_for_links(first.body)


def test_as_text(tip_dir, monkeypatch):
    monkeypatch.setattr(tips, "DOCS_URL", "http://host:8095/")
    text = tips.as_text(tips.load_tips()[0], width=78, color=False)
    lines = text.splitlines()
    assert lines[0] == "First tip"
    assert "Some bold text over two lines." in lines  # wrapped prose, ** removed
    assert "  mot.tweak(0.1)" in lines  # code kept, indented
    assert "More: http://host:8095/scanning.html" in lines


def test_history_back_and_forth(tip_dir):
    h = tips.history
    first = h.next()
    second = h.next()
    third = h.next()
    # all three tips before any repeats
    assert {first.name, second.name, third.name} == {t.name for t in tips.load_tips()}
    assert h.position() == (3, 3)
    assert h.back().name == second.name
    assert h.back().name == first.name
    assert not h.can_back
    assert h.back().name == first.name  # stays at the start
    # forward again walks the same order, nothing new is picked
    assert h.next().name == second.name
    assert h.next().name == third.name
    assert h.position() == (3, 3)
    # past the end: a new random pick, never the current one twice in a row
    fourth = h.next()
    assert fourth.name != third.name
    assert h.position() == (4, 4)


def test_print_tip_records_in_history(tip_dir, capsys):
    shown = tips.print_tip()
    out = capsys.readouterr().out
    assert "Did you know?" in out and shown.title in out
    assert "eco.tip()" in out
    assert tips.history.current.name == shown.name


def test_tip_without_display_prints(tip_dir, capsys):
    shown = tips.tip("tip1", window=False)
    assert shown.title == "Second tip"
    assert "Second tip" in capsys.readouterr().out
    assert tips.history.names == [shown.name]


def test_no_docs_folder_is_quiet(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tips, "TIPS_DIR", tmp_path / "missing")
    monkeypatch.setattr(tips, "history", tips.TipHistory())
    assert tips.load_tips() == []
    assert tips.print_tip() is None
    assert capsys.readouterr().out == ""


def test_window_navigation(tip_dir, monkeypatch):
    pytest.importorskip("qtpy")
    pytest.importorskip("markdown")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from eco.widgets import tip_qt

    tips.print_tip()  # a tip printed earlier comes first
    startup = tips.history.current
    handle = tips.tip(window=True)
    try:
        assert handle._count.text() == "1 of 1 seen"
        assert startup.title in handle._text.toPlainText()
        assert not handle._back.isEnabled()
        handle.next()
        assert handle._count.text() == "2 of 2 seen"
        assert handle._back.isEnabled()
        second = tips.history.current
        handle.back()
        assert startup.title in handle._text.toPlainText()
        handle.next()
        assert tips.history.current.name == second.name  # same one again, not a new pick
        # a second eco.tip() reuses the open window
        assert tips.tip(window=True) is handle
    finally:
        handle.stop()
    assert tip_qt._open_window is None


def test_docs_without_display_prints_url(monkeypatch, capsys):
    monkeypatch.setenv("ECO_NO_X", "1")
    monkeypatch.setattr(tips, "DOCS_URL", "http://host:8095/")
    assert tips.docs() == "http://host:8095/"
    assert tips.docs("../scanning") == "http://host:8095/scanning.html"
    assert capsys.readouterr().out.split() == ["http://host:8095/", "http://host:8095/scanning.html"]


def test_features_is_the_tip_window(tip_dir, monkeypatch, capsys):
    import eco

    monkeypatch.setenv("ECO_NO_X", "1")
    shown = eco.features("tip2")
    assert shown.title == "Third tip"
    assert "Third tip" in capsys.readouterr().out
