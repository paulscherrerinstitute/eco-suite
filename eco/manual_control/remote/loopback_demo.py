"""One-process demo of the full remote split: a server (offline fake
beamline) and the thin-client GUI, wired together over an in-process
socket pair. Proves the entire event-out / state+value-back loop and the
real touchscreen GUI running against the remote client - without any
Bluetooth/USB hardware.

    python -m eco.manual_control.remote.loopback_demo --psi-box

For the non-networked fallback (GUI driving a local box directly, no
link), use:  python -m eco.manual_control.demo
"""

import argparse

from ..demo import build_fake_beamline
from ..mock_gui import ManualControlApp
from .client import RemoteControlClient
from .server import RemoteControlServer
from .transport import socketpair_transports


def main():
    from ..demo import add_view_args, view_kwargs

    args = add_view_args(argparse.ArgumentParser(
        description="offline simulator driving the real client/server split over a socket pair"
    )).parse_args()
    srv_tr, cli_tr = socketpair_transports()
    RemoteControlServer(
        build_fake_beamline(), srv_tr, root_name="beamline",
        step_sizes=[0.001, 0.01, 0.1, 1, 10],
    ).start()
    client = RemoteControlClient(cli_tr).start()
    ManualControlApp(client, **view_kwargs(args)).mainloop()


if __name__ == "__main__":
    main()
