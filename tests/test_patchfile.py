"""`eco.utilities.patchfile`: a failing patch entry must not cost the rest."""

from eco.utilities.patchfile import format_entry_header, run_patch_file, split_patch_file


def _entry(target, body):
    return f"\n{format_entry_header(target, '2026-09-21T10:00:00', 'me')}\n{body}\n"


def _write(tmp_path, *parts):
    p = tmp_path / "patches.py"
    p.write_text("".join(parts))
    return p


def test_failing_entry_is_skipped_and_reported_the_others_run(tmp_path):
    p = _write(
        tmp_path,
        "# preamble\nresults = []\n",
        _entry("a.one", "results.append(1)"),
        _entry("a.bad", "results.append('x')\nraise ValueError('boom')"),
        _entry("a.three", "results.append(3)"),
    )
    out = []
    applied, failed = run_patch_file(p, report=out.append)
    assert applied == ["preamble", "a.one", "a.three"]
    assert failed == ["a.bad"]
    text = "\n".join(out)
    assert "a.bad" in text and "ValueError: boom" in text
    # traceback points at the real line of the file
    assert f'File "{p}", line 9' in text


def test_entries_share_globals_like_the_module_did(tmp_path):
    p = _write(
        tmp_path,
        "import collections\n",
        _entry("a.one", "x = collections.OrderedDict()"),
        _entry("a.two", "assert isinstance(x, collections.OrderedDict)"),
    )
    assert run_patch_file(p, report=lambda m: None)[1] == []


def test_syntax_error_in_one_entry_only_skips_that_entry(tmp_path):
    p = _write(
        tmp_path,
        _entry("a.bad", "def ("),
        _entry("a.good", "ok = True"),
    )
    out = []
    applied, failed = run_patch_file(p, report=out.append)
    assert (applied, failed) == (["a.good"], ["a.bad"])
    assert "SyntaxError" in "\n".join(out)


def test_split_without_headers_is_one_preamble(tmp_path):
    assert [t for t, _, _ in split_patch_file("x = 1\n")] == ["preamble"]
    assert split_patch_file("") == []


def test_writer_and_loader_agree_on_the_header(tmp_path):
    from eco.elements.assembly import Assembly

    top = Assembly(name="top")
    top.patch_file = tmp_path / "patches.py"
    # not interactive -> nothing captured; write a header the way the writer does
    hdr = format_entry_header("top.child", "2026-09-21T10:00:00", "someone")
    assert split_patch_file(f"{hdr}\ny=1\n")[0][0] == "top.child"
