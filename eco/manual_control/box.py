"""ManualControlBox: navigate an eco hierarchy and jog a chosen component.

Hardware- and display-agnostic (no GPIO, no Tkinter here) so the same box
works with the Tkinter mock in mock_gui.py today and with real GPIO
joystick/encoder input on the Pi later, unchanged.

Two decoupled concerns:
- **navigation** (browse the tree via TreeNavigator) - encoder rotate moves
  the cursor, encoder press / touch enters a branch or arms a leaf, and
  you can walk back up. This is what makes it usable on a big structure
  like the `bernina` namespace rather than a flat list.
- **control** - the last leaf you selected becomes the armed `target`; the
  joystick jogs *that*, regardless of where you have since browsed to, via
  Jogger (native motor jog if available, software jog otherwise).
"""

from .constants import MODE_NAVIGATE, MODE_STEP
from .jog import Jogger
from .navigator import Entry, TreeNavigator, is_container, is_controllable, short_name

DEFAULT_STEP_SIZES = [0.001, 0.01, 0.1, 1, 10, 100]


class ManualControlBox:
    def __init__(self, root, root_name=None, step_sizes=None, default_step_index=None):
        self.nav = TreeNavigator(root, root_name=root_name)
        self.step_sizes = list(step_sizes or DEFAULT_STEP_SIZES)
        self.step_index = (
            default_step_index
            if default_step_index is not None
            else len(self.step_sizes) // 2
        )
        self.target = None  # armed Adjustable the joystick jogs
        self.mode = MODE_NAVIGATE
        self._jogger = Jogger(step_size=self.step_size)
        self._entries = self.nav.entries()

    # --- navigation view ----------------------------------------------
    @property
    def entries(self):
        return self._entries

    @property
    def cursor(self):
        return self.nav.cursor

    @property
    def path_names(self):
        return self.nav.path_names

    def refresh(self):
        self._entries = self.nav.entries()
        self.nav.clamp_cursor(len(self._entries))

    def move_cursor(self, direction):
        step = 1 if direction > 0 else -1
        self.nav.cursor = max(
            0, min(self.nav.cursor + step, len(self._entries) - 1)
        )

    def set_cursor(self, index):
        self.nav.cursor = max(0, min(index, len(self._entries) - 1))

    def enter(self):
        """Act on the entry under the cursor. Returns (action, obj):
        action in {"up", "descend", "select", "noop"}."""
        if not self._entries:
            return ("noop", None)
        entry = self._entries[self.nav.cursor]

        if entry.kind == Entry.UP:
            self.nav.go_up()
            self.refresh()
            return ("up", None)

        obj = entry.resolve()
        if is_controllable(obj):
            self.target = obj
            self._jogger.step_size = self.step_size
            return ("select", obj)
        if is_container(obj):
            self.nav.descend(short_name(obj), obj)
            self.refresh()
            return ("descend", obj)
        return ("noop", obj)

    def breadcrumb_jump(self, index):
        self.nav.jump_to(index)
        self.refresh()

    # --- encoder: rotate depends on mode; short press acts / cycles mode;
    # long press disarms ------------------------------------------------
    def encoder_rotate(self, direction):
        if self.mode == MODE_STEP and self.target is not None:
            self.step_up() if direction > 0 else self.step_down()
            return ("step", self.step_size)
        self.move_cursor(direction)
        return ("cursor", self.cursor)

    def encoder_short_press(self):
        """NAVIGATE: act on the cursor (descend/up/arm); arming a leaf
        advances to STEP mode. STEP: go back to NAVIGATE. Returns
        (action, obj)."""
        if self.mode == MODE_STEP:
            self.mode = MODE_NAVIGATE
            return ("mode", MODE_NAVIGATE)
        action, obj = self.enter()
        if action == "select":
            self.mode = MODE_STEP
        return (action, obj)

    def activate_cursor(self):
        """Touch: always act on the cursor entry (never a bare mode
        toggle); arming a leaf switches to STEP, navigating stays/returns
        to NAVIGATE."""
        action, obj = self.enter()
        self.mode = MODE_STEP if action == "select" else MODE_NAVIGATE
        return (action, obj)

    def encoder_long_press(self):
        self.disarm()
        return ("disarm", None)

    def disarm(self):
        self.jog_stop()
        self.target = None
        self.mode = MODE_NAVIGATE

    # --- step size -----------------------------------------------------
    @property
    def step_size(self):
        return self.step_sizes[self.step_index]

    def step_up(self):
        self.step_index = min(self.step_index + 1, len(self.step_sizes) - 1)
        self._jogger.step_size = self.step_size

    def step_down(self):
        self.step_index = max(self.step_index - 1, 0)
        self._jogger.step_size = self.step_size

    def entry_is_armed(self, entry):
        return getattr(entry, "_obj", None) is not None and entry._obj is self.target

    # --- control (joystick jogs the armed target) ----------------------
    def target_name(self):
        return short_name(self.target) if self.target is not None else None

    def target_value(self):
        if self.target is None:
            return None
        return self.target.get_current_value()

    def jog_start(self, direction):
        if self.target is None:
            return
        self._jogger.step_size = self.step_size
        self._jogger.start(self.target, direction)

    def jog_stop(self):
        self._jogger.stop()

    @property
    def is_jogging(self):
        return self._jogger.is_jogging
