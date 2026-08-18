"""Pi-side entry point: connect to the PC's remote control server and show
the touchscreen GUI. Holds no eco - only Python stdlib + Tkinter are needed
on the Pi.

Examples:
    # connect to a PC serving over TCP (local testing)
    python -m eco.manual_control.remote.pi_client --tcp 127.0.0.1 8765

    # connect over a Bluetooth RFCOMM serial device (the real Pi link)
    python -m eco.manual_control.remote.pi_client --serial /dev/rfcomm0

    # connect over a USB-gadget serial device (Pi Zero/4/5; Pi side ttyGS0)
    python -m eco.manual_control.remote.pi_client --serial /dev/ttyGS0
"""

import argparse

from ..mock_gui import ManualControlApp
from .client import RemoteControlClient
from .transport import SerialLineTransport, connect_tcp


def main():
    ap = argparse.ArgumentParser(description="eco manual-control thin client (Pi side)")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--serial", metavar="DEV", help="serial device, e.g. /dev/rfcomm0 or /dev/ttyGS0")
    link.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"), help="connect to HOST PORT (dev only)")
    args = ap.parse_args()

    if args.serial:
        transport = SerialLineTransport(args.serial)
    else:
        host, port = args.tcp
        transport = connect_tcp(host, int(port))

    client = RemoteControlClient(transport).start()
    ManualControlApp(client).mainloop()


if __name__ == "__main__":
    main()
