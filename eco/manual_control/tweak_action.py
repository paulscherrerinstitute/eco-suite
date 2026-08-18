"""A small decorator for turning a single "do one step" method into a
holdable, self-repeating action - independent of eco's own Tweak /
tweak_option machinery in eco.elements.adjustable, which is built around
its own terminal/notebook UI. This one is meant to be driven by physical
(or mocked) buttons: press -> start(), release -> stop().

Usage:

    class Box:
        @tweak_action(interval=0.15, growth=0.85, min_interval=0.03)
        def _step(self, direction):
            item = self.current
            item.set_target_value(item.get_current_value() + direction * self.step_size)

    box = Box()
    box._step.start(+1)   # begins repeating _step(+1) on the box, speeding up
    ...
    box._step.stop()      # stops the repeat
    box._step.tap(+1)     # single immediate call, e.g. for a discrete encoder detent
"""

import threading
import traceback


class _HeldAction:
    """Repeats `step_fn` on a background thread while held, with the
    interval between repeats shrinking geometrically down to `min_interval`
    the longer it is held - a tap moves once, a hold ramps up speed."""

    def __init__(self, step_fn, interval, growth, min_interval):
        self.step_fn = step_fn
        self.interval = interval
        self.growth = growth
        self.min_interval = min_interval
        self._thread = None
        self._stop_event = threading.Event()

    def start(self, *args, **kwargs):
        if self._thread is not None:
            return
        self._stop_event.clear()

        def _run():
            interval = self.interval
            while not self._stop_event.is_set():
                try:
                    self.step_fn(*args, **kwargs)
                except Exception:
                    traceback.print_exc()
                interval = max(self.min_interval, interval * self.growth)
                self._stop_event.wait(interval)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def tap(self, *args, **kwargs):
        """Single immediate step, e.g. for a discrete encoder detent."""
        self.step_fn(*args, **kwargs)

    @property
    def is_running(self):
        return self._thread is not None


class tweak_action:
    """Method decorator. Wraps `step(self, *args, **kwargs)` so that
    accessing it on an instance returns a per-instance `_HeldAction` with
    start()/stop()/tap() instead of calling the method directly."""

    def __init__(self, interval=0.15, growth=0.85, min_interval=0.03):
        self.interval = interval
        self.growth = growth
        self.min_interval = min_interval
        self.step_fn = None

    def __call__(self, step_fn):
        self.step_fn = step_fn
        return self

    def __get__(self, instance, owner):
        if instance is None:
            return self
        cache = instance.__dict__.setdefault("_tweak_actions", {})
        key = self.step_fn.__name__
        if key not in cache:
            bound_step = self.step_fn.__get__(instance, owner)
            cache[key] = _HeldAction(
                bound_step, self.interval, self.growth, self.min_interval
            )
        return cache[key]
