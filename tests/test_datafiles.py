"""Tests for eco.utilities.datafiles (group-writable results under <pgroup>/res).

These deliberately assert on the *mode bits* rather than on group ownership:
the group half needs a real pgroup the test account is a member of, which is a
facility fact, not something a test should depend on. The mode half is where
all the previous bugs were -- the umask silently stripping group write, and a
plain `chmod(0o775)` clearing setgid.
"""

import os
import stat
from pathlib import Path

import pytest

from eco.utilities import datafiles as df


@pytest.fixture(autouse=True)
def _clear_warn_dedup():
    """`warn_once` state is process-global; tests must not inherit each other's."""
    df._warned.clear()
    yield
    df._warned.clear()


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# --------------------------------------------------------------------------
# ensure_dir
# --------------------------------------------------------------------------


def test_ensure_dir_sets_group_write_and_setgid_on_every_created_level(tmp_path):
    """The original bug: `mkdir(parents=True)` created the intermediate levels
    at 0o755, so another pgroup member could not create the next run in them."""
    target = tmp_path / "run_data" / "daq" / "run0001" / "aux"

    df.ensure_dir(target)

    for level in (
        tmp_path / "run_data",
        tmp_path / "run_data" / "daq",
        tmp_path / "run_data" / "daq" / "run0001",
        target,
    ):
        assert level.is_dir()
        assert mode_of(level) & stat.S_IWGRP, f"{level} not group-writable"
        assert mode_of(level) & stat.S_ISGID, f"{level} not setgid"


def test_ensure_dir_beats_the_umask(tmp_path):
    """`mkdir(mode=0o2775)` alone cannot do this: the umask masks the mode."""
    old = os.umask(0o022)
    try:
        df.ensure_dir(tmp_path / "d")
        assert mode_of(tmp_path / "d") == df.DIR_MODE
    finally:
        os.umask(old)


def test_ensure_dir_is_idempotent_on_an_existing_directory(tmp_path):
    d = tmp_path / "existing"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)

    df.ensure_dir(d)

    assert mode_of(d) & stat.S_IWGRP
    assert mode_of(d) & stat.S_ISGID


def test_ensure_dir_only_adds_bits_and_never_removes_them(tmp_path):
    """An admin-widened directory must survive eco touching it."""
    d = tmp_path / "wide"
    d.mkdir()
    os.chmod(d, 0o2777)

    df.ensure_dir(d)

    assert mode_of(d) == 0o2777


def test_ensure_dir_returns_the_path_for_inline_use(tmp_path):
    assert df.ensure_dir(tmp_path / "x") == tmp_path / "x"


# --------------------------------------------------------------------------
# open_group_writable
# --------------------------------------------------------------------------


def test_open_group_writable_creates_a_group_writable_file(tmp_path):
    f = tmp_path / "status.json"

    with df.open_group_writable(f, "w") as fh:
        fh.write("{}")

    assert mode_of(f) == df.FILE_MODE
    assert f.read_text() == "{}"


def test_open_group_writable_creates_the_parent_directory(tmp_path):
    f = tmp_path / "aux" / "deeper" / "status.json"

    with df.open_group_writable(f, "w") as fh:
        fh.write("{}")

    assert f.exists()
    assert mode_of(f.parent) & stat.S_IWGRP


def test_open_group_writable_supports_rewriting_in_place(tmp_path):
    """The `r+` / seek / truncate pattern the daq json writers use."""
    f = tmp_path / "scan_info.json"
    f.write_text("old content that is long")
    os.chmod(f, 0o644)

    with df.open_group_writable(f, "r+") as fh:
        fh.seek(0)
        fh.write("new")
        fh.truncate()

    assert f.read_text() == "new"
    assert mode_of(f) & stat.S_IWGRP


def test_open_group_writable_leaves_an_already_permissive_file_alone(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    os.chmod(f, 0o666)

    with df.open_group_writable(f, "w") as fh:
        fh.write("y")

    assert mode_of(f) == 0o666


def test_open_group_writable_propagates_write_errors(tmp_path):
    """Permission fixing is best effort; the write itself is not."""
    with pytest.raises(OSError):
        with df.open_group_writable(tmp_path / "nodir" / "f", "r"):
            pass


# --------------------------------------------------------------------------
# pgroup detection
# --------------------------------------------------------------------------


def test_pgroup_of_path_finds_the_pgroup_component(monkeypatch):
    monkeypatch.setattr(df, "_gid_of", lambda name: 12345)
    assert (
        df.pgroup_of_path("/sf/bernina/data/p23415/res/run_data/daq/run1/aux/x.json")
        == "p23415"
    )
    assert (
        df.pgroup_of_path("/gpfs/photonics/swissfel/res/bernina/p23415/run_data")
        == "p23415"
    )


def test_pgroup_of_path_ignores_a_pgroup_shaped_name_that_is_not_a_group(monkeypatch):
    monkeypatch.setattr(df, "_gid_of", lambda name: None)
    assert df.pgroup_of_path("/sf/bernina/data/p99999/res") is None


def test_pgroup_of_path_returns_none_outside_a_pgroup_tree(monkeypatch):
    monkeypatch.setattr(df, "_gid_of", lambda name: 12345)
    assert df.pgroup_of_path("/home/someone/notes.txt") is None


def test_levels_below_pgroup_root_stops_at_the_pgroup_directory(monkeypatch):
    monkeypatch.setattr(df, "_gid_of", lambda name: 12345)

    levels = df._levels_below_pgroup_root(
        "/sf/bernina/data/p23415/res/run_data/daq/run0001"
    )

    assert levels[0] == Path("/sf/bernina/data/p23415/res/run_data/daq/run0001")
    assert levels[-1] == Path("/sf/bernina/data/p23415/res")
    assert Path("/sf/bernina/data/p23415") not in levels
    assert Path("/sf/bernina/data") not in levels


def test_levels_below_pgroup_root_never_walks_up_from_an_unknown_layout(monkeypatch):
    """Guards against chmod'ing its way to / when the path is not what we think."""
    monkeypatch.setattr(df, "_gid_of", lambda name: None)
    assert df._levels_below_pgroup_root("/some/where/else") == [Path("/some/where/else")]


# --------------------------------------------------------------------------
# warnings
# --------------------------------------------------------------------------


def test_unfixable_directory_warns_once_and_does_not_raise(tmp_path, monkeypatch, capsys):
    d = tmp_path / "locked"
    d.mkdir()
    os.chmod(d, 0o755)

    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(df.os, "chmod", refuse)
    monkeypatch.setattr(df, "acl_grants_group_write", lambda *a, **k: False)

    assert df.ensure_group_writable(d) is False
    assert df.ensure_group_writable(d) is False  # deduped

    out = capsys.readouterr().out
    assert out.count("not group-writable") == 1
    assert "chmod g+rwXs" in out  # actionable
    assert str(d) in out


def test_no_warning_when_an_acl_already_grants_group_write(tmp_path, monkeypatch, capsys):
    d = tmp_path / "acl"
    d.mkdir()
    os.chmod(d, 0o755)

    def refuse(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(df.os, "chmod", refuse)
    monkeypatch.setattr(df, "acl_grants_group_write", lambda *a, **k: True)

    assert df.ensure_group_writable(d) is True
    assert capsys.readouterr().out == ""


def test_ensure_dir_reports_but_survives_a_failing_chmod(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(df, "acl_grants_group_write", lambda *a, **k: False)
    real_chmod = df.os.chmod

    def refuse(path, *args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    target = tmp_path / "a" / "b"
    monkeypatch.setattr(df.os, "chmod", refuse)

    result = df.ensure_dir(target)

    assert result.is_dir()  # the data still gets written
    assert "not group-writable" in capsys.readouterr().out
    monkeypatch.setattr(df.os, "chmod", real_chmod)


def test_warn_once_reports_only_the_first_occurrence(capsys):
    assert df.warn_once("k", "first") is True
    assert df.warn_once("k", "second") is False
    out = capsys.readouterr().out
    assert "first" in out and "second" not in out


# --------------------------------------------------------------------------
# ACL parsing
# --------------------------------------------------------------------------


def _fake_getfacl(text):
    class _Result:
        stdout = text

    return lambda *a, **k: _Result()


def test_acl_named_group_entry_counts_as_granted(monkeypatch):
    monkeypatch.setattr(
        df.subprocess,
        "run",
        _fake_getfacl("user::rwx\ngroup::r-x\ngroup:p23415:rwx\nmask::rwx\nother::r-x\n"),
    )
    assert df.acl_grants_group_write("/whatever", "p23415") is True


def test_acl_entry_masked_out_does_not_count(monkeypatch):
    monkeypatch.setattr(
        df.subprocess,
        "run",
        _fake_getfacl("user::rwx\ngroup:p23415:rwx\nmask::r-x\nother::r-x\n"),
    )
    assert df.acl_grants_group_write("/whatever", "p23415") is False


def test_plain_group_entry_is_not_an_extra_grant(monkeypatch):
    """`group::` *is* the mode bits we already found lacking -- it must not be
    read as an ACL that makes the warning unnecessary."""
    monkeypatch.setattr(
        df.subprocess, "run", _fake_getfacl("user::rwx\ngroup::rwx\nother::r-x\n")
    )
    assert df.acl_grants_group_write("/whatever", "p23415") is False


def test_default_acl_granting_group_write_counts(monkeypatch):
    monkeypatch.setattr(
        df.subprocess,
        "run",
        _fake_getfacl("user::rwx\ngroup::r-x\ndefault:group:p23415:rwx\n"),
    )
    assert df.acl_grants_group_write("/whatever", "p23415") is True


def test_missing_getfacl_is_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no getfacl here")

    monkeypatch.setattr(df.subprocess, "run", boom)
    assert df.acl_grants_group_write("/whatever", "p23415") is False
