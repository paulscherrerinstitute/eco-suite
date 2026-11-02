"""Where a step scan's time goes: acquiring, moving, settling, callbacks.

``StepScan`` adds every step's durations here (``ScanTiming.add_step``) and
prints ``short()`` at the end of each step line, so the duty cycle -- the
share of the time actually spent acquiring -- is visible while the scan
runs. Callbacks can book their own share under a named category (an adaptive
scan books its planning as ``"planning"``); such a category is part of the
callback time, shown separately in brackets.

Once per scan, a warning is printed when the duty cycle drops below
``WARN_ACQUIRE_BELOW`` after ``WARN_AFTER_STEPS`` steps, naming where the
time goes instead, and when a booked category (planning) takes more than
``WARN_CATEGORY_ABOVE``.
"""

WARN_AFTER_STEPS = 5
WARN_ACQUIRE_BELOW = 0.5
WARN_CATEGORY_ABOVE = 0.2

_PHASES = ("acquire", "move", "settle", "callbacks")
_ADVICE = {
    "move": "fewer, larger steps or a faster axis would help",
    "settle": "check the settling_time of the scan",
    "callbacks": "the per-step callbacks (status, elog, analysis) are slow",
}


class ScanTiming:
    def __init__(self):
        self.totals = dict.fromkeys(_PHASES, 0.0)
        self.booked = {}
        self.n_steps = 0
        self._warned = set()

    def add_step(self, acquire=0.0, move=0.0, settle=0.0, callbacks=0.0):
        for key, value in zip(_PHASES, (acquire, move, settle, callbacks)):
            self.totals[key] += max(float(value), 0.0)
        self.n_steps += 1

    def book(self, category, seconds):
        """Book `seconds` of callback time under `category` (e.g. planning)."""
        self.booked[category] = self.booked.get(category, 0.0) + max(float(seconds), 0.0)

    @property
    def total(self):
        return sum(self.totals.values())

    def fractions(self):
        total = self.total
        if total <= 0:
            return {}
        return {key: value / total for key, value in self.totals.items()}

    def mean_step_time(self):
        return self.total / self.n_steps if self.n_steps else None

    def short(self):
        """One line for the step output, e.g. ``acq 61% move 30% settle 0%
        cb 9% (planning 6%)``."""
        f = self.fractions()
        if not f:
            return ""
        s = (
            f"acq {f['acquire']:.0%} move {f['move']:.0%} "
            f"settle {f['settle']:.0%} cb {f['callbacks']:.0%}"
        )
        if self.booked:
            s += " (" + ", ".join(
                f"{k} {v / self.total:.0%}" for k, v in self.booked.items()
            ) + ")"
        return s

    def summary(self):
        if not self.n_steps:
            return ""
        return (
            f"Scan timing: {self.n_steps} steps in {self.total:.1f} s "
            f"({self.mean_step_time():.2f} s/step): {self.short()}"
        )

    def warnings(self):
        """Messages that are due now; each kind is returned only once per
        scan."""
        out = []
        if self.n_steps < WARN_AFTER_STEPS:
            return out
        f = self.fractions()
        if "duty" not in self._warned and f.get("acquire", 1.0) < WARN_ACQUIRE_BELOW:
            self._warned.add("duty")
            worst = max(("move", "settle", "callbacks"), key=lambda k: f[k])
            out.append(
                f"Warning: only {f['acquire']:.0%} of the scan time is spent acquiring; "
                f"{worst} takes {f[worst]:.0%} ({_ADVICE[worst]})."
            )
        for category, seconds in self.booked.items():
            share = seconds / self.total if self.total else 0.0
            if category not in self._warned and share > WARN_CATEGORY_ABOVE:
                self._warned.add(category)
                out.append(
                    f"Warning: {category} takes {share:.0%} of the scan time."
                )
        return out
