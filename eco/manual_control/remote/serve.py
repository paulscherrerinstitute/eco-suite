"""PC-side entry point: run the eco session behind a remote control server.

Examples:
    # serve the offline fake beamline over TCP (local testing, two processes)
    python -m eco.manual_control.remote.serve --fake --tcp 8765

    # serve the real bernina namespace over a Bluetooth RFCOMM serial device
    python -m eco.manual_control.remote.serve --bernina --serial /dev/rfcomm0

    # serve over a USB-gadget serial device (Pi Zero/4/5; PC side often ttyACM0)
    python -m eco.manual_control.remote.serve --fake --serial /dev/ttyACM0

TCP is for local development only (no WiFi/IP in production); the real link
is --serial (Bluetooth RFCOMM or USB-gadget serial).
"""

import argparse

from .server import RemoteControlServer
from .transport import SerialLineTransport, accept_tcp, listen_tcp


def build_root(args):
    if args.bernina:
        import eco.bernina as bernina

        return bernina.namespace, "bernina"
    return _fake()


def _fake():
    from ..demo import build_fake_beamline

    return build_fake_beamline(), "beamline"


def main():
    ap = argparse.ArgumentParser(description="eco manual-control remote server (PC side)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--bernina", action="store_true", help="serve the real bernina namespace")
    src.add_argument("--fake", action="store_true", help="serve the offline fake beamline (default)")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--tcp", type=int, metavar="PORT", help="listen on 127.0.0.1:PORT (dev only)")
    link.add_argument("--serial", metavar="DEV", help="serial device, e.g. /dev/rfcomm0 or /dev/ttyACM0")
    args = ap.parse_args()

    root, name = build_root(args)

    if args.serial:
        print(f"serving '{name}' over {args.serial} ...")
        server = RemoteControlServer(root, SerialLineTransport(args.serial), root_name=name).start()
        server.wait()
        return

    listener = listen_tcp("127.0.0.1", args.tcp)
    print(f"serving '{name}' on 127.0.0.1:{args.tcp} (Ctrl-C to stop) ...")
    while True:
        tr = accept_tcp(listener)
        print("client connected")
        server = RemoteControlServer(root, tr, root_name=name).start()
        server.wait()
        print("client disconnected")


if __name__ == "__main__":
    main()
