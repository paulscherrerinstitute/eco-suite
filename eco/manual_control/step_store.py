"""Remembers the step size chosen for each adjustable, across sessions.

Picking a sensible step for a motor is a small act of judgement that the
operator should not have to repeat every time they arm it. So the box
stores what was last used, per adjustable, and seeds a first-time step from
the object itself:

    1. what was stored for this adjustable before
    2. the interval its own `tweak` was last set up with, if any
       (`tweak_option` leaves a `Tweak` on the object as `_tweak_instance`)
    3. the middle of the box's default step list

The file is per account (``~/.eco/manual_control_steps.json``) rather than in
a shared config tree: it is a personal convenience, and keeping it out of
the group-writable trees avoids the ownership traps documented in
CLAUDE.md for anything several accounts write to.
"""

import json
import os
import tempfile

DEFAULT_PATH = "~/.eco/manual_control_steps.json"


class StepStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = os.path.expanduser(path) if path else None
        self._data = {}
        self._load()

    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = {k: v for k, v in loaded.items() if isinstance(v, (int, float))}
        except (OSError, ValueError):
            self._data = {}  # a corrupt file must not break the box

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            # write + rename, so a crash cannot leave a half-written file
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass  # remembering a step size is never worth failing a session

    def get(self, key):
        return self._data.get(key)

    def set(self, key, step_size):
        if key is None:
            return
        if self._data.get(key) == step_size:
            return
        self._data[key] = step_size
        self._save()


def tweak_step_size(obj):
    """The step this adjustable's own `tweak` was last set up with, if any."""
    tweak = getattr(obj, "_tweak_instance", None)
    steps = getattr(tweak, "step_sizes", None)
    if not steps:
        return None
    try:
        step = float(steps[0])
    except (TypeError, ValueError, IndexError):
        return None
    return step if step > 0 else None


def initial_step_size(obj, key, store, step_sizes):
    """Stored step > the object's own tweak interval > middle of the list."""
    if store is not None:
        stored = store.get(key)
        if stored:
            return float(stored)
    from_tweak = tweak_step_size(obj)
    if from_tweak:
        return from_tweak
    return step_sizes[len(step_sizes) // 2]
