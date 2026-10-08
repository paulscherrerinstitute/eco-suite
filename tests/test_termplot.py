"""eco.utilities.termplot: stacked text plots and the in-place live region."""

import io

import pytest

from eco.utilities import termplot


def test_live_region_redraws_in_place_when_nothing_scrolled():
    out = io.StringIO()
    region = termplot.LiveRegion(out)
    region.write("help\n")
    region.write("\x1b[2Kstatus 1\r")
    region.show("a\nb")
    first = out.getvalue()
    # first draw: fresh copy, then the status line restored under it
    assert first.endswith("\x1b[2Ka\n\x1b[2Kb\n\x1b[2Kstatus 1\r")
    region.write("\x1b[2Kstatus 2\r")  # status updates with \r only
    region.show("c\nd")
    # nothing scrolled: cursor up 2 lines, rewrite, status line untouched
    assert out.getvalue()[len(first):] == (
        "\x1b[2Kstatus 2\r" + "\r\x1b[2A\x1b[2Kc\n\x1b[2Kd\n"
    )


def test_live_region_prints_a_fresh_copy_after_other_output():
    out = io.StringIO()
    region = termplot.LiveRegion(out)
    region.show("a\nb")
    region.write("enter absolute position\n")
    region.write("\x1b[2Kstatus\r")
    region.show("c\nd")
    assert out.getvalue().endswith("\r\x1b[2K\x1b[2Kc\n\x1b[2Kd\n\x1b[2Kstatus\r")
    region.show("e")  # height changed: fresh copy as well
    assert out.getvalue().endswith("\x1b[2Ke\n\x1b[2Kstatus\r")


def test_live_region_installs_as_stdout(monkeypatch):
    import sys

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    region = termplot.LiveRegion().install()
    assert sys.stdout is region
    print("x")
    region.uninstall()
    assert sys.stdout is out and out.getvalue() == "x\n"


def test_panel_stack_shares_x_and_labels_only_the_bottom():
    pytest.importorskip("uniplot")
    text = termplot.panel_stack(
        [
            {"title": "det", "xs": [0, 1, 2], "ys": [1, 3, 2], "highlight_last": True},
            {"title": "mot", "xs": [0, 1, 2], "ys": [0, 0.5, 1]},
            {"title": "empty", "xs": [], "ys": []},
        ],
        width=40,
        height=3,
    )
    lines = text.split("\n")
    assert [l.strip() for l in lines if l.strip() in ("det", "mot", "empty")] == [
        "det",
        "mot",
        "empty",
    ]
    frame_ends = [i for i, l in enumerate(lines) if l.startswith("└")]
    assert len(frame_ends) == 3
    # x tick labels once, under the last panel only
    assert frame_ends[-1] == len(lines) - 2 and lines[-1].strip().startswith("0")
    assert all(lines[i + 1].strip() in ("mot", "empty") for i in frame_ends[:-1])
    assert len(lines) == 3 * (1 + 2 + 3) + 1
