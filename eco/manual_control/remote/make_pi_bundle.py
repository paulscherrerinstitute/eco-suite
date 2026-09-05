"""Assemble the eco-free thin-client into a standalone `manual_control`
package that runs on the Pi with only Python 3 + Tkinter - no eco, numpy,
dask, or EPICS. Copy the resulting folder to the Pi (e.g. over USB).

    python -m eco.manual_control.remote.make_pi_bundle /path/to/output

The bundle contains only the modules that never import eco (protocol,
transport, client, mock GUI, constants, pi_client entry point), plus the
pi_setup/ provisioning files so the copied folder is everything the device
needs. The PC/eco-side modules (box, navigator, jog, snapshot, server,
demo) are deliberately excluded. All imports inside the bundle are
package-relative, so it works under the top-level name `manual_control`
unchanged.
"""

import argparse
import os
import shutil

PKG_FILES = ["constants.py", "mock_gui.py"]
REMOTE_FILES = [
    "protocol.py",
    "transport.py",
    "client.py",
    "box_link.py",
    "pi_client.py",
    "pi_hardware.py",
    "pi_screen.py",
    "pi_app.py",
]

PKG_INIT = '"""Standalone eco-free Pi thin-client bundle."""\n'
REMOTE_INIT = (
    "from .client import RemoteControlClient\n"
    "from .transport import SerialLineTransport, connect_tcp, socketpair_transports\n"
)


SETUP_DIR = "pi_setup"


def build(dest):
    here = os.path.dirname(os.path.abspath(__file__))  # .../manual_control/remote
    mc = os.path.dirname(here)  # .../manual_control
    out_pkg = os.path.join(dest, "manual_control")
    out_remote = os.path.join(out_pkg, "remote")
    os.makedirs(out_remote, exist_ok=True)

    for f in PKG_FILES:
        shutil.copy2(os.path.join(mc, f), os.path.join(out_pkg, f))
    for f in REMOTE_FILES:
        shutil.copy2(os.path.join(here, f), os.path.join(out_remote, f))
    src_setup = os.path.join(here, SETUP_DIR)
    dst_setup = os.path.join(out_remote, SETUP_DIR)
    if os.path.isdir(src_setup):
        shutil.rmtree(dst_setup, ignore_errors=True)
        shutil.copytree(src_setup, dst_setup)
    with open(os.path.join(out_pkg, "__init__.py"), "w") as fh:
        fh.write(PKG_INIT)
    with open(os.path.join(out_remote, "__init__.py"), "w") as fh:
        fh.write(REMOTE_INIT)
    return out_pkg


def main():
    ap = argparse.ArgumentParser(description="Build the eco-free Pi thin-client bundle")
    ap.add_argument("dest", help="output directory (a manual_control/ package is created inside)")
    args = ap.parse_args()
    out = build(args.dest)
    print("Pi bundle written to:", out)
    print("Copy that manual_control/ folder to the device, then either")
    print("  provision it (networked PSI box):")
    print("    sudo ./manual_control/remote/pi_setup/setup_box.sh <pc-host>")
    print("  or run it by hand:")
    print("    python3 -m manual_control.remote.pi_app --tcp <pc-host> 8791 \\")
    print("        --preset psi-mcu-box --size 800x480 --font-scale 1.4 --fullscreen")


if __name__ == "__main__":
    main()
