"""
Backend-independent core of the tweak panels (Qt: eco.widgets.tweak_panel_qt,
ipywidgets: eco.widgets.tweak_panel_ipy).

The panels offer everything the terminal tweak does -- step *2 / step /2,
neg/pos step, back to start, go to an absolute value, reset the current value
-- for 1 to 4 axes, plus a "keypress control" mode that uses the same keys as
the terminal:

- 1 axis:   left = neg dir, up = step*2, down = step/2, right = pos dir
- 2 axes:   arrows move x (left/right) and y (up/down), ctrl+arrows change
            the step of x (ctrl+right *2, ctrl+left /2) and y (ctrl+up *2,
            ctrl+down /2) -- as in Tweak.xy_adjustable_tweak
- stacked (automatic for 3-4 axes, optional for 1-2): one keyboard row per
            axis, (neg dir, step*2, step/2, pos dir), rows as in
            Tweak.STACKED_KEY_ROWS
- always:   s = all axes back to start, Esc = stop, q = leave keypress control
- 1 axis, arrow mode only: g / r jump into the "go to" / "reset to" field

Nothing here imports Qt or ipywidgets, so the key logic is testable on its own.

Two kinds of axis, same interface:
- SoftTweakAxis drives one adjustable of an eco.elements.adjustable.Tweak via
  set_target_value(), stepping from the last *target* like the terminal tweak
  does (fast repeated keypresses accumulate instead of being lost).
- IocTweakAxis drives a motor record's own tweak fields (TWV/TWF/TWR), as the
  MotorRecord/Smaract _tweak_ioc terminal tweaks do.
"""

import threading


def _fmt(value):
    try:
        return f"{value:1.6g}"
    except Exception:
        return str(value)


def _name_of(obj):
    try:
        if hasattr(obj, "alias") and hasattr(obj.alias, "get_full_name"):
            return obj.alias.get_full_name()
    except Exception:
        pass
    return str(getattr(obj, "name", obj))


class _AxisBase:
    """Common parts; subclasses implement _do_move/_do_set_step/_do_go."""

    def __init__(self, obj, step):
        self.obj = obj
        self.name = _name_of(obj)
        self.step = float(step)
        self.start_value = obj.get_current_value()
        self._changer = None

    def get_value(self):
        return self.obj.get_current_value()

    def current_step(self):
        """The step in effect right now, also if changed elsewhere (terminal
        keys, the IOC); used by the recorder's settle tolerance."""
        return self.step

    def scale_step(self, factor):
        self.set_step(self.step * factor)

    def set_step(self, step):
        self.step = float(step)
        self._do_set_step(self.step)

    def _do_set_step(self, step):
        pass

    def back_to_start(self):
        self.go(self.start_value)

    def reset_current_value_to(self, value):
        obj = self.obj
        if hasattr(obj, "reset_current_value_to"):
            obj.reset_current_value_to(value)
        else:
            changer = obj.set_target_value(value)
            if hasattr(changer, "wait"):
                changer.wait()

    def stop(self):
        for target in (self._changer, self.obj):
            if target is not None and callable(getattr(target, "stop", None)):
                try:
                    target.stop()
                    return
                except Exception:
                    pass

    def add_value_callback(self, callback):
        """Register callback(value) on value changes; returns an undo function,
        or None if the object has no callback support (panels then poll)."""
        obj = self.obj
        if not callable(getattr(obj, "add_value_callback", None)):
            return None
        try:
            index = obj.add_value_callback(
                lambda **kw: callback(kw["value"]) if "value" in kw else callback(None)
            )
        except Exception:
            return None

        def remove():
            try:
                obj.clear_value_callback(index=index)
            except TypeError:
                obj.clear_value_callback()
            except Exception:
                pass

        return remove


class SoftTweakAxis(_AxisBase):
    """Axis `index` of an eco.elements.adjustable.Tweak."""

    def __init__(self, tweak, index):
        self.tweak = tweak
        self.index = index
        super().__init__(tweak.adjs[index], tweak.step_sizes[index])
        self.start_value = tweak.startpositions[index]

    def _do_set_step(self, step):
        self.tweak.set_step_size((self.index, step))

    def current_step(self):
        return self.tweak.step_sizes[self.index]

    def _target(self):
        return self.tweak.target_positions[-1][self.index]

    def _record_target(self, value):
        targets = list(self.tweak.target_positions[-1])
        targets[self.index] = value
        self.tweak.target_positions.append(targets)

    def move(self, sign):
        self.go(self._target() + sign * self.step)

    def go(self, value):
        # only the touched axis is commanded (the terminal tweak re-sends all
        # targets; for a panel that would yank back an axis moved elsewhere)
        self._record_target(value)
        self._changer = self.obj.set_target_value(value)

    def reset_current_value_to(self, value):
        super().reset_current_value_to(value)
        self._record_target(value)


class IocTweakAxis(_AxisBase):
    """A motor record (MotorRecord, SmaractRecord, SmaractStreamdevice, ...)
    tweaked through its own TWV/TWF/TWR fields."""

    def __init__(self, motor, step=None, go_to_current_value_first=False):
        if hasattr(motor, "_motor") and hasattr(motor._motor, "get_pv"):
            self._pv_step = motor._motor.get_pv("TWV")
            self._pv_fwd = motor._motor.get_pv("TWF")
            self._pv_rev = motor._motor.get_pv("TWR")
        else:
            from epics import PV

            self._pv_step = PV(motor.pvname + ":TWV")
            self._pv_fwd = PV(motor.pvname + ":TWF.PROC")
            self._pv_rev = PV(motor.pvname + ":TWR.PROC")
        if not step:
            step = self._pv_step.get()
        try:
            step = float(step)
        except Exception:
            step = 1.0
        super().__init__(motor, step)
        self._pv_step.put(self.step)
        if go_to_current_value_first:
            # as _tweak_ioc does: sync VAL to RBV before the first TWF/TWR,
            # off the GUI thread since it waits for the motor
            def _sync():
                try:
                    motor.set_target_value(motor.get_current_value()).wait()
                except Exception:
                    pass

            threading.Thread(target=_sync, daemon=True).start()

    def _do_set_step(self, step):
        self._pv_step.put(step)

    def current_step(self):
        try:
            value = self._pv_step.get()
            if value is not None:
                return float(value)
        except Exception:
            pass
        return self.step

    def move(self, sign):
        (self._pv_fwd if sign > 0 else self._pv_rev).put(1)

    def go(self, value):
        self._changer = self.obj.set_target_value(value)


# >>> key bindings >>>

# Normalized key names used by both backends: "Left"/"Right"/"Up"/"Down",
# "Ctrl+Left" etc., "Escape", and plain lower-case characters.
ARROWS = ("Left", "Up", "Down", "Right")


def _stacked_rows(n):
    from eco.elements.adjustable import Tweak

    rows = Tweak.STACKED_KEY_ROWS
    if not 1 <= n <= len(rows):
        raise ValueError(f"stacked tweak supports 1 to {len(rows)} axes, got {n}")
    return list(rows[:4] if n == 4 else rows[1 : 1 + n])


def use_stacked(n, stacked=None):
    if n > 4:
        raise ValueError(f"tweak panels support 1 to 4 axes, got {n}")
    return n > 2 if stacked is None else bool(stacked)


def key_bindings(n, stacked=None):
    """{normalized key: action} for n axes. Actions:
    ("move", i, +1/-1), ("step", i, factor), ("start",), ("stop",),
    ("release",), ("focus_go", i), ("focus_reset", i)."""
    b = {"s": ("start",), "q": ("release",), "Escape": ("stop",)}
    if use_stacked(n, stacked):
        for i, (neg, dbl, half, pos) in enumerate(_stacked_rows(n)):
            b[neg] = ("move", i, -1)
            b[dbl] = ("step", i, 2.0)
            b[half] = ("step", i, 0.5)
            b[pos] = ("move", i, +1)
    elif n == 1:
        b.update(
            {
                "Left": ("move", 0, -1),
                "Up": ("step", 0, 2.0),
                "Down": ("step", 0, 0.5),
                "Right": ("move", 0, +1),
                "g": ("focus_go", 0),
                "r": ("focus_reset", 0),
            }
        )
    elif n == 2:
        b.update(
            {
                "Left": ("move", 0, -1),
                "Right": ("move", 0, +1),
                "Down": ("move", 1, -1),
                "Up": ("move", 1, +1),
                "Ctrl+Right": ("step", 0, 2.0),
                "Ctrl+Left": ("step", 0, 0.5),
                "Ctrl+Up": ("step", 1, 2.0),
                "Ctrl+Down": ("step", 1, 0.5),
            }
        )
    else:
        raise ValueError(f"{n} axes need stacked keys")
    return b


def key_manual(names, stacked=None):
    """The key manual as a list of (keys, meaning) rows."""
    n = len(names)
    rows = []
    if use_stacked(n, stacked):
        key_rows = _stacked_rows(n)
        for i in reversed(range(n)):  # top keyboard row first
            neg, dbl, half, pos = key_rows[i]
            rows.append(
                (
                    f"{neg} {dbl} {half} {pos}",
                    f"{names[i]}: {neg} neg dir, {dbl} step*2, {half} step/2, {pos} pos dir",
                )
            )
    elif n == 1:
        rows += [
            ("← →", f"{names[0]}: neg dir / pos dir"),
            ("↑ ↓", "step*2 / step/2"),
            ("g / r", "type a go-to / reset-to value (Enter applies)"),
        ]
    else:
        rows += [
            ("← →", f"x = {names[0]}: neg dir / pos dir"),
            ("↓ ↑", f"y = {names[1]}: neg dir / pos dir"),
            ("ctrl+→ ctrl+←", "x step*2 / step/2"),
            ("ctrl+↑ ctrl+↓", "y step*2 / step/2"),
        ]
    rows += [
        ("s", "all axes back to start values"),
        ("Esc", "stop"),
        ("q", "leave keypress control"),
    ]
    return rows


class TweakController:
    """The axes of one panel, and what a key/button does to them.

    `notify(text)` is how errors and the like are reported back to the
    panel; panels replace it."""

    def __init__(self, axes, stacked=None):
        self.axes = list(axes)
        self.stacked = use_stacked(len(self.axes), stacked)
        self.bindings = key_bindings(len(self.axes), self.stacked)
        self.notify = lambda text: None

    @property
    def names(self):
        return [axis.name for axis in self.axes]

    def manual(self):
        return key_manual(self.names, self.stacked)

    def _guard(self, what, func, *args):
        try:
            func(*args)
            return True
        except Exception as exc:
            self.notify(f"{what} failed: {exc}")
            return False

    def move(self, i, sign):
        return self._guard(f"{self.axes[i].name} move", self.axes[i].move, sign)

    def scale_step(self, i, factor):
        return self._guard(
            f"{self.axes[i].name} step", self.axes[i].scale_step, factor
        )

    def set_step(self, i, step):
        return self._guard(f"{self.axes[i].name} step", self.axes[i].set_step, step)

    def go(self, i, value):
        return self._guard(f"{self.axes[i].name} go", self.axes[i].go, value)

    def reset_current_value_to(self, i, value):
        # may wait for a set; run off the calling (GUI) thread
        def _run():
            self._guard(
                f"{self.axes[i].name} reset",
                self.axes[i].reset_current_value_to,
                value,
            )

        threading.Thread(target=_run, daemon=True).start()

    def back_to_start(self):
        for axis in self.axes:
            self._guard(f"{axis.name} back to start", axis.back_to_start)

    def stop(self):
        for axis in self.axes:
            axis.stop()

    def handle_key(self, key):
        """Apply the action bound to `key`. Returns the action tuple (so the
        panel can handle the UI-only ones: release, focus_go, focus_reset),
        or None if the key isn't bound."""
        action = self.bindings.get(key)
        if action is None:
            return None
        kind = action[0]
        if kind == "move":
            self.move(action[1], action[2])
        elif kind == "step":
            self.scale_step(action[1], action[2])
        elif kind == "start":
            self.back_to_start()
        elif kind == "stop":
            self.stop()
        return action


def axes_from_tweak(tweak):
    return [SoftTweakAxis(tweak, i) for i in range(len(tweak.adjs))]


# >>> front-end selection >>>


def frontend():
    """'qt', 'ipy' or 'terminal': where an interactive tweak should show up.

    - eco desktop console, in-process kernel (InProcessInteractiveShell; the
      terminal KeyPress loop has no tty there) -> 'qt'
    - eco desktop console, subprocess kernel (marked by ECO_QTCONSOLE_KERNEL,
      set in eco.widgets.console_kernel; qtconsole can't render ipywidgets)
      -> 'qt'
    - any other Jupyter kernel (notebook, lab, voila) -> 'ipy'
    - terminal IPython / plain python -> 'terminal'"""
    import os

    try:
        from IPython import get_ipython

        shell = get_ipython()
    except Exception:
        shell = None
    cls = shell.__class__.__name__ if shell is not None else ""
    if cls == "InProcessInteractiveShell":
        return "qt"
    if cls == "ZMQInteractiveShell":
        return "qt" if os.environ.get("ECO_QTCONSOLE_KERNEL") else "ipy"
    return "terminal"


def tweak_panel(axes, stacked=None, backend=None, display=False, detectors=None, **kwargs):
    """A tweak panel for `axes` (SoftTweakAxis/IocTweakAxis objects).

    backend: 'qt', 'ipy' or None (ipywidgets in a Jupyter kernel that isn't
    an eco desktop console, Qt otherwise). A Qt panel always opens its own
    window; an ipywidgets panel is returned for the cell to show, or shown
    right away with display=True.

    detectors: a Detector or a list of them, recorded along the tweak and
    plotted in the panel (see eco.widgets.tweak_recorder); the recorder is
    the panel's .recorder."""
    controller = TweakController(axes, stacked=stacked)
    if detectors is not None:
        from eco.widgets.tweak_recorder import TweakRecorder

        kwargs["recorder"] = TweakRecorder(controller.axes, detectors)
    if backend is None:
        backend = "ipy" if frontend() == "ipy" else "qt"
    if backend == "ipy":
        from eco.widgets.tweak_panel_ipy import tweak_panel_ipy

        return tweak_panel_ipy(controller, display=display, **kwargs)
    from eco.widgets.tweak_panel_qt import show_tweak_panel_qt

    return show_tweak_panel_qt(controller, **kwargs)
