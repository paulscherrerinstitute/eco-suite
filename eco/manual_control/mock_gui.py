"""Tkinter stand-ins for the physical joystick + rotary encoder, plus an
on-screen navigator, so the ManualControlBox can be exercised live on a
workstation before any Raspberry Pi / GPIO hardware is involved.

Navigation works with BOTH inputs:
- touch: tap a breadcrumb segment to jump up; tap a list row to enter a
  branch or arm a leaf; tap the on-screen step -/+ buttons.
- encoder: rotate to move the list cursor, press the center button to
  enter/arm the highlighted row.

The joystick jogs whichever leaf is currently armed. Swapping this module
for a real GPIO joystick/encoder reader driving the same ManualControlBox
is the path to real Pi hardware.
"""

import math
import tkinter as tk

from .constants import MODE_NAVIGATE, MODE_STEP

BG = "#1a1a1a"
PANEL_BG = "#222222"
ACCENT = "#4da3ff"
ARMED = "#ffcc55"
TEXT = "#eeeeee"
MUTED = "#888888"


class DraggableJoystick(tk.Canvas):
    def __init__(
        self, master, on_jog_start=None, on_jog_stop=None, size=170, radius=66, deadzone=0.15
    ):
        super().__init__(master, width=size, height=size, highlightthickness=0, bg=PANEL_BG)
        self.radius = radius
        self.deadzone = deadzone
        self.knob_radius = 17
        self.center = (size / 2, size / 2)
        self.on_jog_start = on_jog_start
        self.on_jog_stop = on_jog_stop
        self._active_direction = None

        cx, cy = self.center
        self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#555", width=2)
        self.create_line(cx - radius, cy, cx + radius, cy, fill="#3a3a3a")
        self.knob = self.create_oval(
            cx - self.knob_radius, cy - self.knob_radius,
            cx + self.knob_radius, cy + self.knob_radius, fill=ACCENT, outline="",
        )
        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_drag(self, event):
        cx, cy = self.center
        dx, dy = event.x - cx, event.y - cy
        dist = math.hypot(dx, dy)
        if dist > self.radius:
            dx, dy = dx * self.radius / dist, dy * self.radius / dist
            dist = self.radius
        self.coords(
            self.knob, cx + dx - self.knob_radius, cy + dy - self.knob_radius,
            cx + dx + self.knob_radius, cy + dy + self.knob_radius,
        )
        magnitude = dist / self.radius
        # up (dy < 0) = increase / forward
        direction = None if magnitude < self.deadzone else (1 if dy < 0 else -1)
        if direction != self._active_direction:
            if self._active_direction is not None and self.on_jog_stop:
                self.on_jog_stop()
            if direction is not None and self.on_jog_start:
                self.on_jog_start(direction)
            self._active_direction = direction

    def _on_release(self, event):
        cx, cy = self.center
        self.coords(
            self.knob, cx - self.knob_radius, cy - self.knob_radius,
            cx + self.knob_radius, cy + self.knob_radius,
        )
        if self._active_direction is not None and self.on_jog_stop:
            self.on_jog_stop()
        self._active_direction = None


class MockEncoder(tk.Canvas):
    def __init__(
        self, master, on_rotate=None, on_press=None, on_long_press=None,
        size=130, detents=12, long_press_ms=600,
    ):
        super().__init__(master, width=size, height=size, highlightthickness=0, bg=PANEL_BG)
        self.center = (size / 2, size / 2)
        self.radius = size / 2 - 14
        self.button_radius = 19
        self.detents = detents
        self.long_press_ms = long_press_ms
        self.on_rotate = on_rotate
        self.on_press = on_press
        self.on_long_press = on_long_press
        self._dragging = False
        self._last_angle = None
        self._accum_deg = 0.0
        self._angle = -90.0
        self._btn_down = False
        self._long_job = None
        self._long_fired = False

        cx, cy = self.center
        self.create_oval(cx - self.radius, cy - self.radius, cx + self.radius, cy + self.radius, outline="#555", width=2)
        self.indicator = self.create_line(cx, cy, cx, cy - self.radius, fill=ACCENT, width=3)
        self.button = self.create_oval(cx - self.button_radius, cy - self.button_radius, cx + self.button_radius, cy + self.button_radius, fill="#333", outline="#777")
        self.create_text(cx, cy, text="OK", fill=MUTED, font=("Helvetica", 9))

        self.bind("<Button-1>", self._on_press_evt)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda e: self._emit_detent(-1))
        self.bind("<Button-5>", lambda e: self._emit_detent(1))

    def _angle_at(self, x, y):
        cx, cy = self.center
        return math.degrees(math.atan2(y - cy, x - cx))

    def _on_press_evt(self, event):
        cx, cy = self.center
        if math.hypot(event.x - cx, event.y - cy) <= self.button_radius:
            self._dragging = False
            self._btn_down = True
            self._long_fired = False
            self.itemconfig(self.button, fill="#444")
            self._long_job = self.after(self.long_press_ms, self._fire_long)
            return
        self._dragging = True
        self._last_angle = self._angle_at(event.x, event.y)

    def _fire_long(self):
        self._long_job = None
        self._long_fired = True
        self.itemconfig(self.button, fill="#5a3a3a")
        if self.on_long_press:
            self.on_long_press()

    def _on_drag(self, event):
        if not self._dragging:
            return
        angle = self._angle_at(event.x, event.y)
        delta = (angle - self._last_angle + 180) % 360 - 180
        self._last_angle = angle
        self._angle = (self._angle + delta) % 360
        self._redraw_indicator()
        step_deg = 360 / self.detents
        self._accum_deg += delta
        while self._accum_deg >= step_deg:
            self._accum_deg -= step_deg
            self._emit_detent(1)
        while self._accum_deg <= -step_deg:
            self._accum_deg += step_deg
            self._emit_detent(-1)

    def _on_release(self, event):
        self._dragging = False
        if self._btn_down:
            self._btn_down = False
            self.itemconfig(self.button, fill="#333")
            if self._long_job is not None:
                self.after_cancel(self._long_job)
                self._long_job = None
                if self.on_press:
                    self.on_press()  # short press

    def _on_wheel(self, event):
        self._emit_detent(1 if event.delta < 0 else -1)

    def _emit_detent(self, direction):
        step_deg = 360 / self.detents
        self._angle = (self._angle + direction * step_deg) % 360
        self._redraw_indicator()
        if self.on_rotate:
            self.on_rotate(direction)

    def _redraw_indicator(self):
        cx, cy = self.center
        rad = math.radians(self._angle)
        self.coords(
            self.indicator, cx, cy,
            cx + self.radius * math.cos(rad), cy + self.radius * math.sin(rad),
        )


class ConnectionRequestDialog(tk.Toplevel):
    """Asks the operator whether an eco session may drive this box.

    Two situations, deliberately worded differently: nobody is connected
    (plain accept), or somebody is (taking over drops them). The person
    holding the box is the only one who can judge that, which is why the
    box - not the calling machine - decides.
    """

    def __init__(self, master, request, current=None, font_scale=1.0, on_answer=None):
        super().__init__(master)
        self.request = request
        self.on_answer = on_answer
        self.answer = None
        self.title("connection request")
        # An accent border around a lighter panel: without a window manager
        # (the box runs fullscreen, undecorated) a plain Toplevel is nearly
        # invisible against the UI behind it.
        DIALOG_BG = "#2e2e2e"
        self.configure(bg=ACCENT)
        self.transient(master)
        try:
            self.grab_set()  # modal: nothing else on the box until answered
        except tk.TclError:
            pass

        def f(size, *style):
            return ("Helvetica", max(7, int(round(size * font_scale))), *style)

        body = tk.Frame(self, bg=DIALOG_BG)
        body.pack(padx=3, pady=3)
        taking_over = current is not None
        tk.Label(body, text="another eco session wants this box" if taking_over
                 else "an eco session wants to connect",
                 fg=ACCENT, bg=DIALOG_BG, font=f(14, "bold")).pack(padx=16, pady=(14, 6))
        tk.Label(body, text=request.describe(), fg=TEXT, bg=DIALOG_BG,
                 font=("DejaVu Sans Mono", max(8, int(12 * font_scale)))).pack(padx=16)
        if taking_over:
            tk.Label(body, text=f"currently driven by\n{current}", fg=MUTED, bg=DIALOG_BG,
                     font=f(10), justify="center").pack(padx=16, pady=(8, 0))
            accept_text, reject_text = "Take over", "Keep current"
        else:
            accept_text, reject_text = "Accept", "Reject"

        row = tk.Frame(body, bg=DIALOG_BG)
        row.pack(padx=16, pady=14)
        tk.Button(row, text=accept_text, font=f(13, "bold"), width=12,
                  command=lambda: self._answer(True)).pack(side="left", padx=6)
        tk.Button(row, text=reject_text, font=f(13), width=12,
                  command=lambda: self._answer(False)).pack(side="left", padx=6)
        tk.Label(body, text="knob press = accept   ·   long press / Esc = decline",
                 fg=MUTED, bg=DIALOG_BG, font=f(8)).pack(pady=(0, 8))

        self.bind("<Return>", lambda e: self._answer(True))
        self.bind("<space>", lambda e: self._answer(True))
        self.bind("<Escape>", lambda e: self._answer(False))
        self.protocol("WM_DELETE_WINDOW", lambda: self._answer(False))
        self.after(50, self._centre)
        self.focus_set()

    def _centre(self):
        self.update_idletasks()
        master = self.master
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # the physical encoder drives it too: press accepts, long press declines
    def encoder_press(self):
        self._answer(True)

    def encoder_long_press(self):
        self._answer(False)

    def _answer(self, accept):
        if self.answer is not None:
            return
        self.answer = accept
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self.on_answer:
            self.on_answer(self.request, accept)


# Default device screen (4" SPI panel). The "device area" of the mock is
# clamped to exactly this, so what you see on a laptop is what fits the Pi.
# Pass screen_size=(w, h) for a different panel - e.g. (800, 480) for the
# official 7" touchscreen of the PSI Motor Control Unit box.
DEVICE_W, DEVICE_H = 480, 320


class ManualControlApp(tk.Tk):
    def __init__(self, box, poll_interval_ms=90, mock=True, screen_size=None,
                 font_scale=1.0, layout=None):
        super().__init__()
        self.box = box
        self.poll_interval_ms = poll_interval_ms
        self.mock = mock
        self.screen_w, self.screen_h = screen_size or (DEVICE_W, DEVICE_H)
        self.font_scale = float(font_scale)
        self.title("eco manual control" + (" - mock" if mock else ""))
        self.configure(bg=BG)

        self.target_var = tk.StringVar()
        self.value_var = tk.StringVar()
        self.step_var = tk.StringVar()
        self.mode_var = tk.StringVar()

        # ===== device screen: exactly what the 480x320 panel shows =====
        # fixed size + no propagation so it never grows past the real panel
        self.device = tk.Frame(
            self, width=self.screen_w, height=self.screen_h, bg=BG,
            highlightthickness=(1 if mock else 0), highlightbackground="#555",
        )
        self.device.pack(side="left")
        self.device.pack_propagate(False)

        # breadcrumb (top, full width in both layouts)
        self.breadcrumb = tk.Frame(self.device, bg=BG)
        self.breadcrumb.pack(anchor="w", fill="x", padx=6, pady=(4, 2))

        # Layout: on a wide landscape panel (the 7" box, held landscape with
        # the joystick/encoder to the RIGHT of the screen) the armed target
        # and its live value belong on the right edge, next to the hand on
        # the controls; the list gets the rest. On a small panel they stack
        # (list above, one status strip below).
        self.landscape = self.screen_w >= 640 if layout is None else (layout == "landscape")
        body = tk.Frame(self.device, bg=BG)
        body.pack(fill="both", expand=True)

        if self.landscape:
            status = tk.Frame(body, bg=PANEL_BG, width=int(self.screen_w * 0.34))
            status.pack(side="right", fill="y")
            status.pack_propagate(False)
            listwrap = tk.Frame(body, bg=BG)
            listwrap.pack(side="left", fill="both", expand=True, padx=6, pady=2)
            self._build_status_column(status)
        else:
            status = tk.Frame(body, bg=PANEL_BG)
            status.pack(side="bottom", fill="x")
            listwrap = tk.Frame(body, bg=BG)
            listwrap.pack(fill="both", expand=True, padx=6, pady=2)
            self._build_status_strip(status)

        # component list (fills the rest)
        scroll = tk.Scrollbar(listwrap)
        scroll.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            listwrap, bg=PANEL_BG, fg=TEXT, font=self._mono(11),
            highlightthickness=0, selectbackground=ACCENT, selectforeground="#000",
            activestyle="none", yscrollcommand=scroll.set, takefocus=False,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.listbox.yview)
        self.listbox.bind("<ButtonRelease-1>", self._on_row_tap)

        # ===== mock-only side panel: mouse joystick + encoder =====
        # (the real device has physical ones, so this area does not exist there)
        if mock:
            ctrl = tk.Frame(self, bg=BG)
            ctrl.pack(side="left", fill="y", padx=10, pady=8)
            tk.Label(ctrl, text="mock controls\n(device uses physical ones)", fg=MUTED, bg=BG, justify="left", font=("Helvetica", 8)).pack(anchor="w")
            self.joystick = DraggableJoystick(ctrl, on_jog_start=self._jog_start, on_jog_stop=self._jog_stop)
            self.joystick.pack(pady=(6, 4))
            self.encoder = MockEncoder(ctrl, on_rotate=self._encoder_rotate, on_press=self._encoder_press, on_long_press=self._encoder_long_press)
            self.encoder.pack(pady=(0, 4))
            tk.Label(ctrl, text="keys ↑↓ jog · ←→ enc\nspace OK · esc disarm · -/+ step", fg=MUTED, bg=BG, justify="left", font=("Helvetica", 8)).pack(anchor="w")

        # jog-key hold state + poll bookkeeping
        self._jog_key_dir = None
        self._jog_stop_job = None
        self._last_struct = None
        self._last_cursor = -1
        self._poll_job = None

        self._bind_keys()
        self.focus_set()
        self._render_nav()
        self._poll()

    def _build_status_strip(self, status):
        """Compact bottom strip: armed + value, step -/+, mode, disarm."""
        r1 = tk.Frame(status, bg=PANEL_BG)
        r1.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(r1, text="armed", fg=MUTED, bg=PANEL_BG, font=self._font(9)).pack(side="left")
        tk.Label(r1, textvariable=self.target_var, fg=ARMED, bg=PANEL_BG, font=self._font(13, "bold")).pack(side="left", padx=(4, 8))
        tk.Label(r1, textvariable=self.value_var, fg=TEXT, bg=PANEL_BG, font=self._font(17, "bold")).pack(side="right")
        r2 = tk.Frame(status, bg=PANEL_BG)
        r2.pack(fill="x", padx=6, pady=(0, 5))
        tk.Button(r2, text="\u2212", width=2, font=self._font(11), command=self._step_down, takefocus=False).pack(side="left")
        tk.Label(r2, textvariable=self.step_var, fg=TEXT, bg=PANEL_BG, width=7, font=self._font(11)).pack(side="left", padx=2)
        tk.Button(r2, text="+", width=2, font=self._font(11), command=self._step_up, takefocus=False).pack(side="left")
        self.disarm_btn = tk.Button(r2, text="Disarm", font=self._font(10), command=self._disarm, state="disabled", takefocus=False)
        self.disarm_btn.pack(side="right")
        tk.Button(r2, text="\u2630", font=self._font(10), command=self._toggle_menu,
                  takefocus=False).pack(side="right", padx=4)
        self.slot_frame = None
        self._slot_labels = []
        tk.Label(r2, textvariable=self.mode_var, fg=ACCENT, bg=PANEL_BG, font=self._font(9, "bold")).pack(side="right", padx=8)

    def _build_status_column(self, status):
        """Right-hand column for the landscape panel: everything the hand on
        the physical controls needs to read while jogging, big and close."""
        tk.Label(status, text="armed", fg=MUTED, bg=PANEL_BG, font=self._font(10)).pack(anchor="w", padx=8, pady=(8, 0))
        tk.Label(status, textvariable=self.target_var, fg=ARMED, bg=PANEL_BG, font=self._font(15, "bold"),
                 wraplength=int(self.screen_w * 0.30), justify="left").pack(anchor="w", padx=8)
        tk.Label(status, textvariable=self.value_var, fg=TEXT, bg=PANEL_BG, font=self._font(26, "bold"),
                 wraplength=int(self.screen_w * 0.30), justify="left").pack(anchor="w", padx=8, pady=(6, 2))
        tk.Label(status, textvariable=self.mode_var, fg=ACCENT, bg=PANEL_BG, font=self._font(11, "bold"),
                 wraplength=int(self.screen_w * 0.30), justify="left").pack(anchor="w", padx=8)

        # armed axes: which one the stick drives, and each one's step size
        self.slot_frame = tk.Frame(status, bg=PANEL_BG)
        self.slot_frame.pack(anchor="w", fill="x", padx=8, pady=(8, 0))
        self._slot_labels = []

        steprow = tk.Frame(status, bg=PANEL_BG)
        steprow.pack(anchor="w", fill="x", padx=8, pady=(10, 0))
        tk.Label(steprow, text="step", fg=MUTED, bg=PANEL_BG, font=self._font(10)).pack(side="left")
        tk.Label(steprow, textvariable=self.step_var, fg=TEXT, bg=PANEL_BG, font=self._font(13)).pack(side="right")
        btnrow = tk.Frame(status, bg=PANEL_BG)
        btnrow.pack(anchor="w", fill="x", padx=8, pady=(2, 0))
        tk.Button(btnrow, text="\u2212", font=self._font(15), width=3, command=self._step_down, takefocus=False).pack(side="left", expand=True, fill="x")
        tk.Button(btnrow, text="+", font=self._font(15), width=3, command=self._step_up, takefocus=False).pack(side="right", expand=True, fill="x")

        bottom = tk.Frame(status, bg=PANEL_BG)
        bottom.pack(side="bottom", fill="x", padx=8, pady=8)
        tk.Button(bottom, text="\u2630 Menu", font=self._font(13), command=self._toggle_menu,
                  takefocus=False).pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.disarm_btn = tk.Button(bottom, text="Disarm", font=self._font(13),
                                    command=self._disarm, state="disabled", takefocus=False)
        self.disarm_btn.pack(side="right", expand=True, fill="x")

    def _font(self, size, *style):
        """Font tuple scaled by font_scale (bigger text for finger-touch)."""
        return ("Helvetica",) + (max(6, int(round(size * self.font_scale))),) + style

    def _mono(self, size):
        return ("DejaVu Sans Mono", max(6, int(round(size * self.font_scale))))

    def _toggle_menu(self):
        toggle = getattr(self.box, "toggle_menu", None)
        if toggle is not None:
            toggle()

    def _render_slots(self):
        """One line per armed axis; the active one (stick) is highlighted."""
        if self.slot_frame is None:
            return
        slots = list(getattr(self.box, "slots", []) or [])
        active = getattr(self.box, "active_slot", 0)
        if len(self._slot_labels) != len(slots):
            for widget in self.slot_frame.winfo_children():
                widget.destroy()
            self._slot_labels = [
                tk.Label(self.slot_frame, bg=PANEL_BG, anchor="w", justify="left",
                         font=self._font(9))
                for _ in slots
            ]
            for label in self._slot_labels:
                label.pack(anchor="w", fill="x")
        for index, (slot, label) in enumerate(zip(slots, self._slot_labels)):
            name = slot["name"] if isinstance(slot, dict) else slot.name
            step = slot["step"] if isinstance(slot, dict) else slot.step_size
            motion = slot["motion"] if isinstance(slot, dict) else slot.motion
            on_stick = index == active
            marker = "\u25b8" if on_stick else " "
            label.config(
                text=f"{marker} {name}  {step:g}  {motion}",
                fg=ARMED if on_stick else MUTED,
            )

    def _bind_keys(self):
        self.bind_all("<KeyPress-Up>", lambda e: self._key_jog_press(1))
        self.bind_all("<KeyRelease-Up>", lambda e: self._key_jog_release(1))
        self.bind_all("<KeyPress-Down>", lambda e: self._key_jog_press(-1))
        self.bind_all("<KeyRelease-Down>", lambda e: self._key_jog_release(-1))
        self.bind_all("<Left>", lambda e: self._encoder_rotate(-1))
        self.bind_all("<Right>", lambda e: self._encoder_rotate(1))
        self.bind_all("<space>", lambda e: self._encoder_press())
        self.bind_all("<Escape>", lambda e: self._disarm())
        self.bind_all("<m>", lambda e: self._toggle_menu())
        for k in ("<minus>", "<KP_Subtract>", "<bracketleft>"):
            self.bind_all(k, lambda e: self._step_down())
        for k in ("<plus>", "<equal>", "<KP_Add>", "<bracketright>"):
            self.bind_all(k, lambda e: self._step_up())

    def _key_jog_press(self, direction):
        if self._jog_stop_job is not None:
            self.after_cancel(self._jog_stop_job)
            self._jog_stop_job = None
        if self._jog_key_dir != direction:
            if self._jog_key_dir is not None:
                self.box.jog_stop()
            self.box.jog_start(direction)
            self._jog_key_dir = direction

    def _key_jog_release(self, direction):
        # Delay the stop: an auto-repeat KeyPress arriving within the window
        # cancels it, so a held key keeps jogging instead of stuttering.
        if self._jog_stop_job is not None:
            self.after_cancel(self._jog_stop_job)
        self._jog_stop_job = self.after(70, lambda: self._key_jog_stop(direction))

    def _key_jog_stop(self, direction):
        self._jog_stop_job = None
        if self._jog_key_dir == direction:
            self.box.jog_stop()
            self._jog_key_dir = None

    # --- navigation rendering ---
    def _render_nav(self):
        for w in self.breadcrumb.winfo_children():
            w.destroy()
        for i, name in enumerate(self.box.path_names):
            if i:
                tk.Label(self.breadcrumb, text="›", fg=MUTED, bg=BG).pack(side="left")
            tk.Button(
                self.breadcrumb, text=name, relief="flat", bg=BG, fg=ACCENT,
                activebackground=BG, activeforeground=TEXT, bd=0, padx=2, takefocus=False,
                font=self._font(10, "bold" if i == len(self.box.path_names) - 1 else "normal"),
                command=lambda i=i: self._breadcrumb_jump(i),
            ).pack(side="left")

        self.listbox.delete(0, tk.END)
        for entry in self.box.entries:
            label = f" {entry.marker}  {entry.name}"
            if self.box.entry_is_armed(entry):
                label += "   ◀ armed"
            self.listbox.insert(tk.END, label)
        self._sync_cursor()

    def _sync_cursor(self):
        self.listbox.selection_clear(0, tk.END)
        if self.box.entries:
            self.listbox.selection_set(self.box.cursor)
            self.listbox.see(self.box.cursor)

    # --- input handlers (thin: mutate/forward only; the poll re-renders,
    # which keeps local box and remote client identical) ---
    def _on_row_tap(self, event):
        index = self.listbox.nearest(event.y)
        if index < 0:
            return
        self.box.set_cursor(index)
        self.box.activate_cursor()
        self.focus_set()  # keep key control after a mouse/touch tap

    def _encoder_rotate(self, direction):
        self.box.encoder_rotate(direction)

    def _encoder_press(self):
        self.box.encoder_short_press()

    def _encoder_long_press(self):
        self.box.encoder_long_press()

    def _disarm(self):
        self.box.disarm()

    def _breadcrumb_jump(self, index):
        self.box.breadcrumb_jump(index)

    def _step_up(self):
        self.box.step_up()

    def _step_down(self):
        self.box.step_down()

    def _jog_start(self, direction):
        self.box.jog_start(direction)

    def _jog_stop(self):
        self.box.jog_stop()

    # --- target panel ---
    def _update_target(self):
        if self.box.target is None:
            self.target_var.set("(none - arm one)")
            self.value_var.set("")
            self.disarm_btn.config(state="disabled")
        else:
            self.target_var.set(self.box.target_name())
            self.disarm_btn.config(state="normal")
            try:
                v = self.box.target_value()
                self.value_var.set(f"{v:.4g}" if isinstance(v, (int, float)) else str(v))
            except Exception as exc:
                self.value_var.set(f"<error: {exc}>")
        step = self.box.step_size
        self.step_var.set(f"{step:g}" if isinstance(step, (int, float)) else "…")

        if self.box.mode == MODE_STEP:
            self.mode_var.set("MODE: STEP  (turn = step size)")
        else:
            self.mode_var.set("MODE: NAVIGATE  (turn = browse)")

    def _nav_struct(self):
        return (
            tuple(self.box.path_names),
            tuple((e.name, e.marker, self.box.entry_is_armed(e)) for e in self.box.entries),
        )

    def _poll(self):
        # Re-render on change only (works whether state changes locally from
        # input handlers or arrives asynchronously from the remote link).
        struct = self._nav_struct()
        if struct != self._last_struct:
            self._last_struct = struct
            self._render_nav()
        elif self.box.cursor != self._last_cursor:
            self._sync_cursor()
        self._last_cursor = self.box.cursor
        self._render_slots()
        self._update_target()
        self._poll_job = self.after(self.poll_interval_ms, self._poll)

    def destroy(self):
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        super().destroy()
