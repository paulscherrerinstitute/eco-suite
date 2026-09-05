"""Minimal box-side entry point: one session, no accept dialog, no hardware.

For the real box use pi_app (it listens, asks the operator, and supports
hand-over between sessions). This one is the small debugging cousin: it
takes whatever link it is given and shows the GUI.

Examples:
    # accept the first eco session that calls this box, then serve it
    python -m manual_control.remote.pi_client --listen 8791

    # serial link instead (Bluetooth pendant build)
    python -m manual_control.remote.pi_client --serial /dev/rfcomm0
"""

import argparse

from ..mock_gui import ManualControlApp
from .client import RemoteControlClient
from .pi_app import parse_size
import time

from .transport import SerialLineTransport


def main():
    ap = argparse.ArgumentParser(description="eco manual-control thin client (Pi side)")
    link = ap.add_mutually_exclusive_group(required=True)
    link.add_argument("--serial", metavar="DEV", help="serial device, e.g. /dev/rfcomm0")
    link.add_argument("--listen", type=int, metavar="PORT", help="wait for an eco session on PORT")
    ap.add_argument("--token", metavar="STR", help="shared secret the server requires")
    ap.add_argument("--size", type=parse_size, default=None, metavar="WxH", help="panel size, e.g. 800x480")
    ap.add_argument("--font-scale", type=float, default=1.0, help="scale all text")
    args = ap.parse_args()

    if args.serial:
        transport = SerialLineTransport(args.serial)
    else:
        from .box_link import BoxListener

        listener = BoxListener(port=args.listen, token=args.token)
        print("waiting for an eco session to call ...")
        while True:
            request = listener.pending()
            if request is not None:
                print(f"accepting {request.describe()}")
                transport = request.accept()
                break
            time.sleep(0.3)
        listener.close()

    client = RemoteControlClient(transport, token=args.token).start()
    ManualControlApp(client, screen_size=args.size, font_scale=args.font_scale).mainloop()


if __name__ == "__main__":
    main()
