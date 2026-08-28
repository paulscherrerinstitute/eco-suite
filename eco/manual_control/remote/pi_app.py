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

from ..mock_gui import ManualControlApp
from .client import RemoteControlClient
from .pi_hardware import HardwareInput
from .pi_screen import ScreenPower
from .transport import SerialLineTransport, connect_tcp_retry


def parse_size(text):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


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

    if args.serial:
        transport = SerialLineTransport(args.serial)
    else:
        host, port = args.tcp
        # Retry forever: the box autostarts on power-up (PoE) and must come
        # back on its own when the eco session it serves is restarted.
        transport = connect_tcp_retry(host, int(port))

    client = RemoteControlClient(transport, token=token).start()
    screen = ScreenPower(timeout=args.idle_blank)
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
    # any touch / key / motion wakes the backlight
    for ev in ("<Any-KeyPress>", "<Button>", "<Motion>"):
        app.bind_all(ev, screen.wake, add="+")

    try:
        app.mainloop()
    finally:
        hardware.close()
        screen.close()


if __name__ == "__main__":
    main()
