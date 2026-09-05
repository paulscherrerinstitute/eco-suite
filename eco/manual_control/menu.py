"""The box's menu, expressed as ordinary list entries.

A menu screen is just a different set of entries in the same list the tree
navigation already uses, so the knob, the touchscreen and the remote
protocol drive it with no new concepts: rotate moves the cursor, press
activates, long press goes back. Nothing here knows about Tkinter or GPIO.

Every action runs on the console (the box holds no eco), so a menu item is
simply a callable operating on the ManualControlBox.
"""

SUBMENU = "›"  # >  leads to another menu screen
ACTION = "·"  # ·  does something and stays/returns
BACK = "←"  # <-  up one level
CHECK = "✓"  # v  currently selected value


class MenuEntry:
    """Same shape as a navigator Entry, so the GUI renders it unchanged."""

    kind = "menu"

    def __init__(self, name, action=None, marker=ACTION):
        self.name = name
        self.marker = marker
        self.action = action
        self._obj = None  # never "armed"

    def resolve(self):
        return None

    def __repr__(self):
        return f"<MenuEntry {self.name!r}>"


def _fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def root_menu(box):
    """Top level: everything the armed axis can be told to do."""
    slot = box.active
    items = []
    if slot is not None:
        items.append(MenuEntry(f"step size: {_fmt(slot.step_size)}",
                               lambda b: b.open_menu(step_menu, "step size"), SUBMENU))
        items.append(MenuEntry(f"motion: {slot.motion}",
                               lambda b: b.toggle_motion(), ACTION))
    if box.slots:
        items.append(MenuEntry(f"stick axis: {slot.name if slot else '-'}",
                               lambda b: b.open_menu(slots_menu, "stick axis"), SUBMENU))
    # Arming happens in the tree, so this just gets out of the way rather
    # than pretending to be a submenu.
    items.append(MenuEntry(f"arm another axis: back to the tree "
                           f"({len(box.slots)}/{box.max_slots} armed)",
                           lambda b: b.close_menu(), BACK))
    if slot is not None and box.has_memories(slot):
        items.append(MenuEntry("memories",
                               lambda b: b.open_menu(memories_menu, "memories"), SUBMENU))
    if slot is not None:
        items.append(MenuEntry(f"release {slot.name}", lambda b: b.release_slot(), ACTION))
    items.append(MenuEntry("close menu", lambda b: b.close_menu(), BACK))
    return items


def step_menu(box):
    """Pick the step size for the axis the stick is driving."""
    slot = box.active
    items = [MenuEntry(BACK + " back", lambda b: b.menu_back(), BACK)]
    for size in box.step_sizes:
        marker = CHECK if slot is not None and size == slot.step_size else ACTION
        items.append(MenuEntry(_fmt(size),
                               lambda b, s=size: b.set_step_size(s), marker))
    return items


def slots_menu(box):
    """Choose which armed axis the joystick drives."""
    items = [MenuEntry(BACK + " back", lambda b: b.menu_back(), BACK)]
    for index, slot in enumerate(box.slots):
        marker = CHECK if index == box.active_slot else ACTION
        items.append(MenuEntry(f"{slot.name}   (step {_fmt(slot.step_size)}, {slot.motion})",
                               lambda b, i=index: b.select_slot(i), marker))
    return items


def memories_menu(box):
    """This adjustable's stored memories: recall one, or save the current state."""
    items = [MenuEntry(BACK + " back", lambda b: b.menu_back(), BACK)]
    try:
        entries = box.list_memories()
    except Exception as exc:  # a broken memory dir must not kill the menu
        return items + [MenuEntry(f"cannot read memories: {exc}", None, ACTION)]
    for index, label in entries:
        items.append(MenuEntry(label,
                               lambda b, i=index, l=label: b.open_menu(
                                   confirm_recall_menu(i, l), f"recall {l}"), SUBMENU))
    items.append(MenuEntry("save current state as a new memory",
                           lambda b: b.save_memory(), ACTION))
    if not entries:
        items.insert(1, MenuEntry("(none stored yet)", None, ACTION))
    return items


def confirm_recall_menu(index, label):
    """Recalling moves hardware, so it gets a deliberate second tap."""

    def build(box):
        return [
            MenuEntry(BACK + " no, go back", lambda b: b.menu_back(), BACK),
            MenuEntry(f"YES - recall '{label}' (moves the hardware)",
                      lambda b: b.recall_memory(index), ACTION),
        ]

    return build
