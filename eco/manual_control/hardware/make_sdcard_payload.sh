#!/bin/bash
# Build everything that has to go onto the SD card of the eco control box.
#
#   ./make_sdcard_payload.sh <output-dir> <pc-host> [port]
#
# Produces a folder you copy onto the card's BOOT partition (the small FAT
# one, mounted automatically on any laptop - no root, no image surgery):
#
#   eco_control_box.tar.gz   the eco-free thin client + provisioning files
#   firstrun_eco.sh          optional first-boot hook (see its header)
#   eco_pc_host              which PC/port the box should call
#   README.txt               the two ways to install from here
#
# Deliberately NOT a pre-baked .img: flashing stock Raspberry Pi OS with
# Raspberry Pi Imager lets you set hostname/user/SSH/locale in the dialog
# (which a shared image would have to hard-code, credentials included),
# and this payload then does the eco-specific half.
set -euo pipefail

OUT="${1:-}"; PC_HOST="${2:-}"; PC_PORT="${3:-8791}"
if [[ -z "$OUT" || -z "$PC_HOST" ]]; then
    echo "usage: $0 <output-dir> <pc-host-running-eco> [port]" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # .../manual_control/hardware
REPO="$(cd "$HERE/../../.." && pwd)"                         # repo root (contains eco/)
PY="${PYTHON:-python3}"

mkdir -p "$OUT"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

PYTHONPATH="$REPO:${PYTHONPATH:-}" "$PY" -m eco.manual_control.remote.make_pi_bundle "$STAGE" >/dev/null
tar -czf "$OUT/eco_control_box.tar.gz" -C "$STAGE" manual_control
cp "$REPO/eco/manual_control/remote/pi_setup/firstrun_eco.sh" "$OUT/"
printf '%s %s\n' "$PC_HOST" "$PC_PORT" > "$OUT/eco_pc_host"

cat > "$OUT/README.txt" <<TXT
eco control box - SD card payload for $PC_HOST:$PC_PORT

1. Flash Raspberry Pi OS (WITH desktop - the box needs X for the
   touchscreen GUI) using Raspberry Pi Imager. In its settings dialog set
   hostname, the user, and enable SSH. No Wi-Fi needed: the box is on
   Ethernet/PoE.
2. Copy the files next to this README onto the card's boot partition.
3. Then either:

   a) hands-off: append to the single line of cmdline.txt
        systemd.run=/boot/firmware/firstrun_eco.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
      boot the box once; it installs itself and reboots into the app.

   b) over the network (simpler to debug): boot, ssh in, then
        sudo mkdir -p /opt/eco-control-box
        sudo tar -xzf /boot/firmware/eco_control_box.tar.gz -C /opt/eco-control-box
        sudo /opt/eco-control-box/manual_control/remote/pi_setup/setup_box.sh $PC_HOST $PC_PORT

   Either way the script prints a shared token - pass the same string to
   the PC side:
        python -m eco.manual_control.remote.serve --bernina --tcp $PC_PORT \\
            --bind 0.0.0.0 --token '<that token>'
TXT

echo "payload written to $OUT:"
ls -l "$OUT"
