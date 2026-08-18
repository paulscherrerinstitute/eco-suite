"""The pendant application: connect to the PC over the (Bluetooth by
default) serial link, show the touchscreen GUI, wire in the physical
encoder + joystick, and blank the backlight when idle to save battery.

Runs on the Pi (fullscreen on the 4" display) and, unchanged, on a laptop
for testing (hardware backend + backlight control simply stay inactive and
you drive it by touch/keyboard).

    # on the device: Bluetooth link, fullscreen, blank after 60 s idle
    python3 -m manual_control.remote.pi_app --serial /dev/rfcomm0 --fullscreen --idle-blank 60

    # USB-gadget-serial instead of Bluetooth:
    python3 -m manual_control.remote.pi_app --serial /dev/ttyGS0 --fullscreen

    # laptop test against a PC serving over TCP:
    python3 -m manual_control.remote.pi_app --tcp 127.0.0.1 8791
"""

import argparse

from ..mock_gui import ManualControlApp
from .client import RemoteControlClient
from .pi_hardware import HardwareInput
from .pi_screen import ScreenPower
from .transport import SerialLineTransport, connect_tcp


def main():
    ap = argparse.ArgumentParser(description="eco manual-control pendant app (Pi side)")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--serial", metavar="DEV", help="serial device, e.g. /dev/rfcomm0 or /dev/ttyGS0")
    link.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"), help="connect to HOST PORT (testing)")
    ap.add_argument("--fullscreen", action="store_true", help="fullscreen (for the device touchscreen)")
    ap.add_argument("--idle-blank", type=float, default=0, metavar="SEC",
                    help="blank the backlight after SEC seconds idle (0 = never)")
    args = ap.parse_args()

    if args.serial:
        transport = SerialLineTransport(args.serial)
    else:
        host, port = args.tcp
        transport = connect_tcp(host, int(port))

    client = RemoteControlClient(transport).start()
    screen = ScreenPower(timeout=args.idle_blank)
    hardware = HardwareInput(client, on_activity=screen.wake)

    # fullscreen on the real 480x320 panel = device layout (no mock mouse
    # widgets, physical controls do the input); windowed = mock for laptops.
    app = ManualControlApp(client, mock=not args.fullscreen)
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
