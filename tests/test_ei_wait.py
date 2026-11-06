"""eco.ei_wait: the spinner and the occasional animation while %ei waits."""

import io
import random
import re
import time

import eco.ei as ei
import eco.ei_wait as ew


def _screen(data):
    """The lines a terminal shows after `data` (newlines, \\r, cursor up, clear below)."""
    screen, row, col = [[]], 0, 0
    for tok in re.findall(r"\x1b\[\??\d*[A-Za-z]|\n|.", data, re.S):
        if tok.startswith("\x1b"):
            m = re.match(r"\x1b\[(\??)(\d*)([A-Za-z])", tok)
            if m.group(3) == "A":
                row = max(0, row - int(m.group(2) or 1))
            elif m.group(3) == "J":
                del screen[row][col:]
                del screen[row + 1:]
        elif tok == "\n":
            row, col = row + 1, 0
            if len(screen) <= row:
                screen.append([])
        elif tok == "\r":
            col = 0
        else:
            line = screen[row]
            line[col:col + 1] = [tok]
            col += 1
    return ["".join(line) for line in screen]


def test_animations_have_a_steady_height_and_fit_a_terminal():
    for name, (delay, frames) in ew.ANIMATIONS.items():
        assert 0.05 <= delay <= 1 and len(frames) >= 5, name
        assert len({f.count("\n") for f in frames}) == 1, name  # no jumping lines
        assert ew.width(name) < 60, name


def test_animations_are_occasional_and_never_the_same_twice_in_a_row():
    rng = random.Random(1)
    picks = [ew.pick(0.25, rng) for _ in range(400)]
    shown = [p for p in picks if p]
    assert 60 < len(shown) < 140
    assert set(shown) == set(ew.ANIMATIONS)
    assert all(a != b for a, b in zip(shown, shown[1:]))
    assert ew.pick(0, rng) is None


def test_a_frame_is_drawn_in_place_and_erased(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: shutil.os.terminal_size((120, 40)))
    out = io.StringIO()
    out.write("In [1]: %ei what?\n")
    w = ew.Waiting(out, animation="chicken and egg").start()
    time.sleep(1.6)  # three frames
    w.stop()
    w.stop()  # twice is harmless
    out.write("the answer")
    text = out.getvalue()
    assert text.count("\x1b[J") >= 3 and text.endswith("\x1b[?25hthe answer")
    assert _screen(text) == ["In [1]: %ei what?", "the answer"]


def test_a_narrow_terminal_gets_the_spinner(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: shutil.os.terminal_size((30, 40)))
    out = io.StringIO()
    w = ew.Waiting(out, label="asking m ...", animation="undulator").start()
    time.sleep(0.25)
    w.stop()
    assert w.animation is None and "asking m ..." in out.getvalue()
    assert _screen(out.getvalue()) == [""]


class _SlowBackend:
    def chat(self, msgs, on_delta=None, stop=None):
        time.sleep(0.4)
        on_delta("The answer.")
        return "The answer."


def _assistant(tmp_path, share):
    cfg = ei.load_config(tmp_path / "ei.json")
    cfg.update(scilog_default=None, elog_logbooks=None, wait_animations=share, render="plain")
    a = ei.Assistant(config=cfg, backend=_SlowBackend(), db_path=tmp_path / "i.sqlite")
    a._indexed_at = 1e18
    a.retrieve = lambda q, follow_up=True: []
    return a


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_the_answer_replaces_the_waiting_in_a_terminal_only(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(ew, "in_terminal", lambda out=None: isinstance(out, _Tty))
    tty = _Tty()
    monkeypatch.setattr(sys, "stdout", tty)
    ei._print_answer(_assistant(tmp_path, 1.0), "what?")
    screen = _screen(tty.getvalue())
    assert screen[0] == "The answer." and "\x1b[J" in tty.getvalue()

    plain = io.StringIO()
    monkeypatch.setattr(sys, "stdout", plain)
    ei._print_answer(_assistant(tmp_path, 1.0), "what?")
    assert "\x1b" not in plain.getvalue() and plain.getvalue().startswith("The answer.")


class _MarkdownBackend:
    def __init__(self, lines=3):
        self.text = "\n".join(f"**line {i}**" for i in range(lines))

    def chat(self, msgs, on_delta=None, stop=None):
        for piece in self.text.split(" "):
            on_delta(piece + " ")
        return self.text


def test_with_glow_the_answer_is_previewed_then_rendered_whole(tmp_path, monkeypatch):
    import shutil
    import sys

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(ew, "in_terminal", lambda out=None: isinstance(out, _Tty))
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: shutil.os.terminal_size((80, 20)))
    monkeypatch.setattr(ei, "glow_path", lambda: "/usr/bin/glow")
    monkeypatch.setattr(ei, "glow", lambda text, width, config=None: "  RENDERED\n  " + text.replace("**", "") + "\n\n")
    previews = []
    real = ew.Waiting._draw
    monkeypatch.setattr(ew.Waiting, "_draw", lambda self, frame: previews.append(list(self.preview)) or real(self, frame))

    class Slow(_MarkdownBackend):
        def chat(self, msgs, on_delta=None, stop=None):
            for line in self.text.split("\n"):
                on_delta(line + "\n")
                time.sleep(0.03)
            return self.text

    def run(backend, render="auto"):
        a = _assistant(tmp_path, 0)
        a.config["render"] = render
        a._backend = backend
        tty = _Tty()
        monkeypatch.setattr(sys, "stdout", tty)
        tty.write("In [1]: %ei q\n")
        ei._print_answer(a, "q")
        return _screen(tty.getvalue())

    screen = run(Slow(30))  # taller than the screen: rendered whole all the same
    assert screen[:3] == ["In [1]: %ei q", "  RENDERED", "  line 0"] and "line 29" in screen
    assert not any("**" in line for line in screen) and any(line.startswith("-- ") for line in screen)
    assert any(p and p[-1].startswith("**line") for p in previews) and max(map(len, previews)) <= ei.PREVIEW_LINES

    screen = run(_MarkdownBackend(3), render="plain")  # streamed as it comes
    assert screen[1] == "**line 0**" and "  RENDERED" not in screen


def test_glow_renders_when_it_is_there():
    import pytest

    if ei.glow_path() is None:
        pytest.skip("no glow here")
    out = ei.glow("# Title\n\nsome **bold** text", 80, {"glow_style": "dark"})
    assert "Title" in out and "**" not in out and "\x1b[" in out


def test_the_wait_is_shown_from_the_start_and_steps_aside_for_messages(tmp_path, monkeypatch):
    import shutil
    import sys

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(ew, "in_terminal", lambda out=None: isinstance(out, _Tty))
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *a: shutil.os.terminal_size((100, 40)))
    a = _assistant(tmp_path, 0)
    a._indexed_at = 0
    a.config["reindex_interval"] = 0
    labels = []

    def refresh_index(force=False, report=print):
        time.sleep(0.3)
        report("eco.ei: a message while indexing")
        time.sleep(0.3)

    a.refresh_index = refresh_index
    tty = _Tty()
    monkeypatch.setattr(sys, "stdout", tty)
    tty.write("In [1]: %ei q\n")
    real = ew.Waiting._draw
    monkeypatch.setattr(ew.Waiting, "_draw", lambda self, frame: labels.append(self.label) or real(self, frame))
    ei._print_answer(a, "q")
    screen = _screen(tty.getvalue())
    assert screen[:3] == ["In [1]: %ei q", "eco.ei: a message while indexing", "The answer."]
    assert labels[0] in ("getting ready", "updating the index") and "updating the index" in labels
    assert "Did you know?" not in tty.getvalue()
