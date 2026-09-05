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

from . import menu as menu_mod
from .constants import MODE_NAVIGATE, MODE_STEP
from .jog import Jogger
from .navigator import Entry, TreeNavigator, is_container, is_controllable, short_name
from .step_store import StepStore, initial_step_size

DEFAULT_STEP_SIZES = [0.001, 0.01, 0.1, 1, 10, 100]
DEFAULT_MAX_SLOTS = 4

MOTION_JOG = "jog"  # hold the stick, the axis keeps moving
MOTION_STEP = "step"  # hold the stick, the axis moves in step_size hops


class Slot:
    """One armed adjustable, with its own step size and motion mode.

    Several axes stay armed at once so the operator can switch the stick
    between them (sample x/y/z, say) without walking the tree again - and
    each keeps the step size that suits it, which is the whole point of
    having separate slots rather than one global step.
    """

    def __init__(self, obj, name, key, step_size, motion=MOTION_JOG):
        self.obj = obj
        self.name = name
        self.key = key  # stable id used to remember the step size
        self.step_size = step_size
        self.motion = motion

    def __repr__(self):
        return f"<Slot {self.name} step={self.step_size:g} {self.motion}>"


class ManualControlBox:
    def __init__(self, root, root_name=None, step_sizes=None, default_step_index=None,
                 max_slots=DEFAULT_MAX_SLOTS, step_store=None):
        self.nav = TreeNavigator(root, root_name=root_name)
        self.step_sizes = list(step_sizes or DEFAULT_STEP_SIZES)
        self.default_step_index = (
            default_step_index
            if default_step_index is not None
            else len(self.step_sizes) // 2
        )
        self.max_slots = max_slots
        self.store = StepStore() if step_store is None else step_store
        self.slots = []  # armed axes; the stick drives self.active
        self.active_slot = 0
        self.mode = MODE_NAVIGATE
        self._jogger = Jogger(step_size=self.step_sizes[self.default_step_index])
        self._entries = self.nav.entries()
        # menu state: a stack of (build_callable, title); empty = tree view
        self._menu_stack = []
        self._menu_entries = []
        self._menu_cursor = 0
        self.message = None  # one-line feedback for the box screen

    # --- slots ---------------------------------------------------------
    @property
    def active(self):
        """The slot the joystick drives, or None."""
        if not self.slots:
            return None
        self.active_slot = min(self.active_slot, len(self.slots) - 1)
        return self.slots[self.active_slot]

    @property
    def target(self):
        """The armed Adjustable (kept for everything that predates slots)."""
        slot = self.active
        return None if slot is None else slot.obj

    def _slot_key(self, name):
        return ".".join(list(self.nav.path_names) + [name])

    def _arm(self, obj):
        """Arm `obj`: reuse its slot, take a free one, else replace the active."""
        name = short_name(obj)
        for index, slot in enumerate(self.slots):
            if slot.obj is obj:
                self.active_slot = index
                return slot
        key = self._slot_key(name)
        slot = Slot(obj, name, key,
                    initial_step_size(obj, key, self.store, self.step_sizes))
        if len(self.slots) < self.max_slots:
            self.slots.append(slot)
            self.active_slot = len(self.slots) - 1
        else:
            self.slots[self.active_slot] = slot
        return slot

    def select_slot(self, index):
        if 0 <= index < len(self.slots):
            self.jog_stop()
            self.active_slot = index
            self.close_menu()

    def release_slot(self, index=None):
        """Take an axis out of the slots (the stick moves to the next one)."""
        index = self.active_slot if index is None else index
        if 0 <= index < len(self.slots):
            self.jog_stop()
            self.slots.pop(index)
            self.active_slot = max(0, min(self.active_slot, len(self.slots) - 1))
        self.close_menu()

    def toggle_motion(self):
        slot = self.active
        if slot is not None:
            slot.motion = MOTION_STEP if slot.motion == MOTION_JOG else MOTION_JOG
        self.refresh_menu()

    def set_step_size(self, size):
        slot = self.active
        if slot is not None:
            slot.step_size = size
            self.store.set(slot.key, size)
            self._jogger.step_size = size
        self.menu_back()

    # --- menu ----------------------------------------------------------
    @property
    def in_menu(self):
        return bool(self._menu_stack)

    def open_menu(self, build=None, title="menu"):
        """Enter the menu (or a submenu). `build(box) -> [MenuEntry]`."""
        self.jog_stop()
        self._menu_stack.append((build or menu_mod.root_menu, title))
        self._menu_cursor = 0
        self.refresh_menu()

    def toggle_menu(self):
        self.close_menu() if self.in_menu else self.open_menu()

    def refresh_menu(self):
        if self._menu_stack:
            build, _ = self._menu_stack[-1]
            self._menu_entries = build(self)
            self._menu_cursor = min(self._menu_cursor, max(len(self._menu_entries) - 1, 0))

    def menu_back(self):
        if self._menu_stack:
            self._menu_stack.pop()
        self._menu_cursor = 0
        self.refresh_menu()

    def close_menu(self):
        self._menu_stack = []
        self._menu_entries = []
        self._menu_cursor = 0

    # --- memories (all executed here, on the console) -------------------
    def _memory_of(self, slot):
        return getattr(slot.obj, "memory", None) if slot is not None else None

    def has_memories(self, slot=None):
        return self._memory_of(slot or self.active) is not None

    def list_memories(self):
        memory = self._memory_of(self.active)
        if memory is None:
            return []
        labels = []
        for index, mem in enumerate(memory.memories()):
            date = str(mem.get("date", ""))[:16]
            message = str(mem.get("message", "") or "").strip()
            labels.append((index, f"{date}  {message}"[:60].strip()))
        return labels

    def recall_memory(self, index):
        memory = self._memory_of(self.active)
        if memory is None:
            return
        try:
            memory(index)
            self.message = f"recalled memory {index}"
        except Exception as exc:
            self.message = f"recall failed: {exc}"
        self.close_menu()

    def save_memory(self, message="saved from the control box"):
        memory = self._memory_of(self.active)
        if memory is None:
            return
        try:
            memory.memorize(message=message, force_message=False, to_elog=False)
            self.message = "memory saved"
        except Exception as exc:
            self.message = f"save failed: {exc}"
        self.refresh_menu()

    # --- the current view: the tree, or a menu screen -------------------
    @property
    def entries(self):
        return self._menu_entries if self.in_menu else self._entries

    @property
    def cursor(self):
        return self._menu_cursor if self.in_menu else self.nav.cursor

    @property
    def path_names(self):
        """Breadcrumb: the tree path, plus the menu screens on top of it."""
        if self.in_menu:
            return list(self.nav.path_names) + [title for _, title in self._menu_stack]
        return self.nav.path_names

    def refresh(self):
        self._entries = self.nav.entries()
        self.nav.clamp_cursor(len(self._entries))

    def move_cursor(self, direction):
        step = 1 if direction > 0 else -1
        if self.in_menu:
            self._menu_cursor = max(
                0, min(self._menu_cursor + step, len(self._menu_entries) - 1))
            return
        self.nav.cursor = max(
            0, min(self.nav.cursor + step, len(self._entries) - 1)
        )

    def set_cursor(self, index):
        if self.in_menu:
            self._menu_cursor = max(0, min(index, len(self._menu_entries) - 1))
            return
        self.nav.cursor = max(0, min(index, len(self._entries) - 1))

    def enter(self):
        """Act on the entry under the cursor. Returns (action, obj):
        action in {"up", "descend", "select", "menu", "noop"}."""
        if self.in_menu:
            if not self._menu_entries:
                return ("noop", None)
            item = self._menu_entries[self._menu_cursor]
            if item.action is not None:
                item.action(self)
                self.refresh_menu()
            return ("menu", None)
        if not self._entries:
            return ("noop", None)
        entry = self._entries[self.nav.cursor]

        if entry.kind == Entry.UP:
            self.nav.go_up()
            self.refresh()
            return ("up", None)

        obj = entry.resolve()
        if is_controllable(obj):
            self._arm(obj)
            self._jogger.step_size = self.step_size
            return ("select", obj)
        if is_container(obj):
            self.nav.descend(short_name(obj), obj)
            self.refresh()
            return ("descend", obj)
        return ("noop", obj)

    def breadcrumb_jump(self, index):
        if self.in_menu:
            depth = len(self.nav.path_names)
            if index >= depth:  # a menu level: pop back to it
                while len(self._menu_stack) > index - depth + 1:
                    self._menu_stack.pop()
                self._menu_cursor = 0
                self.refresh_menu()
                return
            self.close_menu()
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
        """Back out: one menu level, or (in the tree) disarm."""
        if self.in_menu:
            self.menu_back()
            return ("menu_back", None)
        self.disarm()
        return ("disarm", None)

    def disarm(self):
        """Release every armed axis (the stick has nothing to drive)."""
        self.jog_stop()
        self.slots = []
        self.active_slot = 0
        self.mode = MODE_NAVIGATE

    # --- step size (per armed axis) -------------------------------------
    @property
    def step_size(self):
        slot = self.active
        return slot.step_size if slot is not None else self.step_sizes[self.default_step_index]

    def _shift_step(self, direction):
        """Move to the next/previous size in the list, from wherever we are."""
        slot = self.active
        if slot is None:
            return
        sizes = sorted(set(self.step_sizes + [slot.step_size]))
        index = sizes.index(slot.step_size)
        index = max(0, min(index + direction, len(sizes) - 1))
        slot.step_size = sizes[index]
        self.store.set(slot.key, slot.step_size)
        self._jogger.step_size = slot.step_size
        if self.in_menu:
            self.refresh_menu()

    def step_up(self):
        self._shift_step(1)

    def step_down(self):
        self._shift_step(-1)

    def entry_is_armed(self, entry):
        obj = getattr(entry, "_obj", None)
        return obj is not None and any(slot.obj is obj for slot in self.slots)

    # --- control (joystick jogs the armed target) ----------------------
    def target_name(self):
        return short_name(self.target) if self.target is not None else None

    def target_value(self):
        if self.target is None:
            return None
        return self.target.get_current_value()

    def jog_start(self, direction):
        slot = self.active
        if slot is None:
            return
        self._jogger.step_size = slot.step_size
        # "jog" uses the adjustable's own continuous jog when it has one;
        # "step" forces the stepped move, which is what you want on things
        # where continuous motion is unhelpful or unsupported.
        self._jogger.start(slot.obj, direction, native=slot.motion == MOTION_JOG)

    def jog_stop(self):
        self._jogger.stop()

    @property
    def is_jogging(self):
        return self._jogger.is_jogging
