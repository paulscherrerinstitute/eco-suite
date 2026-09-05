"""The control-box application: the box LISTENS, eco sessions call it.

The box belongs to no console. Any machine holding the shared token may
offer itself; the operator standing at the box accepts or declines, and can
hand the box from one session to another without touching either machine.

    # on the box (the systemd unit does this)
    python3 -m manual_control.remote.pi_app --listen 8791 \
        --token-file /etc/eco-control-box.token \
        --preset psi-mcu-box --size 800x480 --font-scale 1.4 --fullscreen

    # on a laptop, to try the UI with no hardware (any incoming session)
    python3 -m manual_control.remote.pi_app --listen 8791 --size 800x480

Two situations, both answered on the box itself:
  1. nothing connected - a session calls, the box asks "Accept / Reject"
  2. already connected - another session calls, the box asks "Take over /
     Keep current"; taking over tells the displaced session why, so it
     closes itself instead of lingering.

A serial link (--serial, the Bluetooth pendant build) still works and skips
the whole question, since that link has exactly one possible peer.
"""

import argparse
import os
import socket
import subprocess
import threading
import tkinter as tk

from ..mock_gui import ACCENT, BG, MUTED, TEXT, ConnectionRequestDialog, ManualControlApp
from .box_link import BoxListener, say_bye
from .client import RemoteControlClient
from .pi_hardware import HardwareInput
from .pi_screen import ScreenPower
from .transport import SerialLineTransport

DEFAULT_PORT = 8791


def parse_size(text):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def _own_addresses():
    """hostname + IPv4s - the box must advertise where sessions should call."""
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                             capture_output=True, text=True, timeout=5).stdout
        addrs = [line.split()[3] for line in out.splitlines() if len(line.split()) > 3]
    except (OSError, subprocess.SubprocessError, IndexError):
        addrs = []
    return socket.gethostname(), addrs or ["no address"]


class WaitingScreen(tk.Tk):
    """Shown while no eco session is driving the box.

    Without it the box shows a bare desktop whenever nothing is connected -
    indistinguishable from a broken box. It says where to call it, and
    raises the accept dialog when someone does.
    """

    def __init__(self, listener, screen_size=None, font_scale=1.0, fullscreen=False):
        super().__init__()
        self.listener = listener
        self.font_scale = font_scale
        self.accepted_transport = None
        self.title("eco control box - waiting")
        self.configure(bg=BG)
        w, h = screen_size or (480, 320)
        self.geometry(f"{w}x{h}")
        if fullscreen:
            self.attributes("-fullscreen", True)
            self.config(cursor="none")

        def f(size, *style):
            return ("Helvetica", max(6, int(round(size * font_scale))), *style)

        name, addrs = _own_addresses()
        tk.Label(self, text="waiting for an eco session", fg=ACCENT, bg=BG,
                 font=f(18, "bold")).pack(pady=(int(h * 0.12), 4))
        tk.Label(self, text=f"{name}:{listener.port}", fg=TEXT, bg=BG,
                 font=("DejaVu Sans Mono", max(8, int(14 * font_scale)))).pack()
        self.status = tk.Label(self, text="nothing connected", fg=MUTED, bg=BG, font=f(11))
        self.status.pack(pady=10)
        tk.Label(self, text=f"this box: {'  '.join(addrs)}", fg=MUTED, bg=BG,
                 font=f(9)).pack(side="bottom", pady=6)
        tk.Label(self, fg=MUTED, bg=BG, font=f(9), justify="center",
                 text="connect from any console with:  "
                      "bernina.manual_control_box.start()").pack(side="bottom")

        self.dialog = None
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(300, self._poll)

    def _poll(self):
        if self.accepted_transport is None:
            if self.dialog is None:
                request = self.listener.pending()
                if request is not None:
                    self.dialog = ConnectionRequestDialog(
                        self, request, current=None, font_scale=self.font_scale,
                        on_answer=self._answer)
            self.after(400, self._poll)

    def _answer(self, request, accept):
        self.dialog = None
        if accept:
            self.accepted_transport = request.accept()
            self.destroy()
        else:
            request.reject()
            self.status.config(text="last request declined")


def _watch_for_takeover(app, listener, client, font_scale, state):
    """While connected, offer any new caller the take-over question."""
    if state.get("dialog") is None:
        request = listener.pending()
        if request is not None:
            state["dialog"] = ConnectionRequestDialog(
                app, request, current=state.get("current", "another session"),
                font_scale=font_scale,
                on_answer=lambda req, accept: _answer_takeover(app, client, state, req, accept))
    if not client.connected:
        app.destroy()
        return
    app.after(500, _watch_for_takeover, app, listener, client, font_scale, state)


def _answer_takeover(app, client, state, request, accept):
    state["dialog"] = None
    if not accept:
        request.reject("the box stayed with the current session")
        return
    # tell the displaced session why, so it closes itself, then switch
    say_bye(client.tr, f"the box was handed to {request.who}")
    state["next_transport"] = request.accept()
    app.destroy()


def main():
    ap = argparse.ArgumentParser(description="eco manual-control box app (box side)")
    link = ap.add_mutually_exclusive_group()
    link.add_argument("--listen", type=int, default=DEFAULT_PORT, metavar="PORT",
                      help=f"port eco sessions call (default {DEFAULT_PORT})")
    link.add_argument("--serial", metavar="DEV", help="serial link, e.g. /dev/rfcomm0")
    ap.add_argument("--token", metavar="STR", help="shared secret callers must present")
    ap.add_argument("--token-file", metavar="PATH", help="read the secret from PATH")
    ap.add_argument("--preset", default=None, help="hardware wiring preset: psi-mcu-box | pendant")
    # Axis direction depends on how the stick is mounted; these override the
    # preset without editing code, so a wrong guess is a service-file edit.
    ap.add_argument("--invert-y", dest="invert_y", action="store_true", default=None,
                    help="flip the jog axis (stick up/down)")
    ap.add_argument("--no-invert-y", dest="invert_y", action="store_false",
                    help="do not flip the jog axis")
    ap.add_argument("--invert-x", dest="invert_x", action="store_true", default=None,
                    help="flip the navigate axis (stick left/right)")
    ap.add_argument("--no-invert-x", dest="invert_x", action="store_false",
                    help="do not flip the navigate axis")
    ap.add_argument("--size", type=parse_size, default=None, metavar="WxH",
                    help="panel size, e.g. 800x480 (default 480x320)")
    ap.add_argument("--font-scale", type=float, default=1.0,
                    help="scale all text (1.4 suits finger touch on the 7\" panel)")
    ap.add_argument("--layout", choices=("landscape", "stacked"), default=None)
    ap.add_argument("--fullscreen", action="store_true", help="fullscreen on the touchscreen")
    ap.add_argument("--idle-blank", type=float, default=0, metavar="SEC",
                    help="blank the backlight after SEC seconds idle (0 = never)")
    args = ap.parse_args()

    token = args.token
    if args.token_file:
        with open(args.token_file) as fh:
            token = fh.read().strip()

    hw_overrides = {k: v for k, v in (("invert_x", args.invert_x),
                                      ("invert_y", args.invert_y)) if v is not None}

    screen = ScreenPower(timeout=args.idle_blank)
    listener = None if args.serial else BoxListener(port=args.listen, token=token)
    transport = SerialLineTransport(args.serial) if args.serial else None
    current_who = None

    try:
        while True:
            if transport is None:
                waiting = WaitingScreen(listener, screen_size=args.size,
                                        font_scale=args.font_scale,
                                        fullscreen=args.fullscreen)
                waiting.mainloop()
                transport = waiting.accepted_transport
                if transport is None:
                    break  # operator quit the box app
                current_who = getattr(waiting, "who", None)

            client = RemoteControlClient(transport).start()
            hardware = HardwareInput(client, on_activity=screen.wake,
                                     preset=args.preset, config=hw_overrides)
            app = ManualControlApp(client, mock=not args.fullscreen, screen_size=args.size,
                                   font_scale=args.font_scale, layout=args.layout)
            if args.fullscreen:
                app.attributes("-fullscreen", True)
                app.config(cursor="none")
            for ev in ("<Any-KeyPress>", "<Button>", "<Motion>"):
                app.bind_all(ev, screen.wake, add="+")

            state = {"dialog": None, "next_transport": None, "current": current_who}
            if listener is not None:
                app.after(500, _watch_for_takeover, app, listener, client,
                          args.font_scale, state)
            try:
                app.mainloop()
            finally:
                hardware.close()

            transport = state["next_transport"]
            if transport is None and args.serial:
                break  # a serial link is reopened by systemd, not here
    finally:
        if listener is not None:
            listener.close()
        screen.close()


if __name__ == "__main__":
    main()
