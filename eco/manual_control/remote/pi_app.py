"""The control-box application: connect to the eco session on the PC, show
the touchscreen GUI, wire in the physical encoder + joystick, and blank the
backlight when idle (battery builds only).

Two hardware targets, one program:

    # PSI "Motor Control Unit" box: Pi 3B, 7" touchscreen (800x480,
    # landscape, controls on the right), MCP3008 joystick, PoE Ethernet
    python3 -m manual_control.remote.pi_app --tcp pc-host 8791 \
        --preset psi-mcu-box --size 800x480 --font-scale 1.4 \
        --fullscreen --token-file /etc/eco-pendant.token

    # battery pendant (Pi Zero 2 W, 4" SPI panel, Bluetooth RFCOMM)
    python3 -m manual_control.remote.pi_app --serial /dev/rfcomm0 \
        --fullscreen --idle-blank 60

    # laptop test against a PC serving over TCP (no hardware needed)
    python3 -m manual_control.remote.pi_app --tcp 127.0.0.1 8791 --size 800x480
"""

import argparse
import os
import socket
import subprocess
import threading
import tkinter as tk

from ..mock_gui import ACCENT, BG, MUTED, TEXT, ManualControlApp
from .client import RemoteControlClient
from .pi_hardware import HardwareInput
from .pi_screen import ScreenPower
from .transport import SerialLineTransport, connect_tcp


def parse_size(text):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def _own_addresses():
    """hostname + IPv4s, so the waiting screen is also a diagnostic."""
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                             capture_output=True, text=True, timeout=5).stdout
        addrs = [line.split()[3] for line in out.splitlines() if len(line.split()) > 3]
    except (OSError, subprocess.SubprocessError, IndexError):
        addrs = []
    return socket.gethostname(), addrs or ["no address"]


class WaitingScreen(tk.Tk):
    """Shown while the eco session is not reachable.

    Without this the box shows a bare desktop whenever the PC side is not
    serving - indistinguishable from a broken box. It says what it is
    waiting for, and closes itself the moment the link comes up.
    """

    def __init__(self, host, port, screen_size=None, font_scale=1.0, fullscreen=False):
        super().__init__()
        self.title("eco control box - waiting")
        self.configure(bg=BG)
        w, h = screen_size or (480, 320)
        self.geometry(f"{w}x{h}")
        if fullscreen:
            self.attributes("-fullscreen", True)
            self.config(cursor="none")
        f = lambda size, *st: ("Helvetica", max(6, int(round(size * font_scale))), *st)

        tk.Label(self, text="waiting for the eco session", fg=ACCENT, bg=BG,
                 font=f(18, "bold")).pack(pady=(int(h * 0.12), 4))
        tk.Label(self, text=f"{host}:{port}", fg=TEXT, bg=BG,
                 font=("DejaVu Sans Mono", max(8, int(14 * font_scale)))).pack()
        self.status = tk.Label(self, text="connecting ...", fg=MUTED, bg=BG, font=f(11))
        self.status.pack(pady=10)
        name, addrs = _own_addresses()
        tk.Label(self, text=f"this box: {name}  {'  '.join(addrs)}", fg=MUTED, bg=BG,
                 font=f(9)).pack(side="bottom", pady=6)
        tk.Label(self, fg=MUTED, bg=BG, font=f(9), justify="center",
                 text="start it there with:  bernina.namespace.start_eco_control_box()"
                 ).pack(side="bottom")

        self.transport = None
        self._tries = 0
        self._stop = threading.Event()
        self.bind("<Escape>", lambda e: self._abort())
        threading.Thread(target=self._connect_loop, args=(host, int(port)), daemon=True).start()
        self.after(300, self._poll)

    def _connect_loop(self, host, port):
        while not self._stop.is_set():
            try:
                self.transport = connect_tcp(host, port)
                return
            except OSError:
                self._tries += 1
                self._stop.wait(3.0)

    def _poll(self):
        if self.transport is not None:
            self.destroy()
            return
        self.status.config(text=f"connecting ... (attempt {self._tries + 1})")
        self.after(500, self._poll)

    def _abort(self):
        self._stop.set()
        self.destroy()


def connect_showing_screen(host, port, **kwargs):
    """Block until connected, showing why on the box's own screen."""
    screen = WaitingScreen(host, port, **kwargs)
    screen.mainloop()
    return screen.transport


def main():
    ap = argparse.ArgumentParser(description="eco manual-control box app (Pi side)")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--serial", metavar="DEV", help="serial device, e.g. /dev/rfcomm0 or /dev/ttyGS0")
    link.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"), help="connect to HOST PORT (Ethernet/PoE box)")
    ap.add_argument("--token", metavar="STR", help="shared secret the server requires")
    ap.add_argument("--token-file", metavar="PATH", help="read the shared secret from PATH (first line)")
    ap.add_argument("--preset", default=None, help="hardware wiring preset: psi-mcu-box | pendant")
    ap.add_argument("--size", type=parse_size, default=None, metavar="WxH",
                    help="panel size, e.g. 800x480 (default 480x320)")
    ap.add_argument("--font-scale", type=float, default=1.0,
                    help="scale all text (1.4 is comfortable for finger touch on the 7\" panel)")
    ap.add_argument("--layout", choices=("landscape", "stacked"), default=None,
                    help="force a layout (default: landscape for panels >= 640 px wide)")
    ap.add_argument("--fullscreen", action="store_true", help="fullscreen (for the device touchscreen)")
    ap.add_argument("--idle-blank", type=float, default=0, metavar="SEC",
                    help="blank the backlight after SEC seconds idle (0 = never; battery builds)")
    args = ap.parse_args()

    token = args.token
    if args.token_file:
        with open(args.token_file) as fh:
            token = fh.read().strip()

    screen = ScreenPower(timeout=args.idle_blank)

    # Outer loop: waiting screen -> control UI -> (link dropped) -> waiting
    # screen again. The box autostarts on power-up and must survive the eco
    # session on the PC being started, restarted or stopped at any time.
    while True:
        if args.serial:
            transport = SerialLineTransport(args.serial)
        else:
            host, port = args.tcp
            transport = connect_showing_screen(
                host, port, screen_size=args.size, font_scale=args.font_scale,
                fullscreen=args.fullscreen,
            )
            if transport is None:  # Escape pressed on the waiting screen
                break

        client = RemoteControlClient(transport, token=token).start()
        hardware = HardwareInput(client, on_activity=screen.wake, preset=args.preset)

        # fullscreen on the real panel = device layout (no mock mouse widgets,
        # physical controls do the input); windowed = mock for laptops.
        app = ManualControlApp(
            client, mock=not args.fullscreen, screen_size=args.size,
            font_scale=args.font_scale, layout=args.layout,
        )
        if args.fullscreen:
            app.attributes("-fullscreen", True)
            app.config(cursor="none")
        for ev in ("<Any-KeyPress>", "<Button>", "<Motion>"):
            app.bind_all(ev, screen.wake, add="+")

        def watch_link():
            if not client.connected:
                print("link lost - back to the waiting screen")
                app.destroy()
                return
            app.after(1000, watch_link)

        app.after(1000, watch_link)
        try:
            app.mainloop()
        finally:
            hardware.close()
        if args.serial:  # a serial link is reopened by systemd, not here
            break
    screen.close()


if __name__ == "__main__":
    main()
