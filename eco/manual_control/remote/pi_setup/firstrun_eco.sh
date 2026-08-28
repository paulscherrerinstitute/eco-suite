#!/bin/bash
# Zero-touch first-boot hook for the eco control box.
#
# Put this file plus eco_control_box.tar.gz on the SD card's BOOT partition
# (the small FAT one, visible when you plug the card into any computer) and
# add to the END of the single line in cmdline.txt:
#
#   systemd.run=/boot/firmware/firstrun_eco.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
#
# (older images: /boot/firstrun_eco.sh). It unpacks the bundle, runs
# setup_box.sh and removes itself from cmdline.txt, so the next boot is a
# normal boot with the box app autostarting.
set -eux

BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
PC_HOST_FILE="$BOOT/eco_pc_host"          # one line: "host [port]"
read -r PC_HOST PC_PORT < "$PC_HOST_FILE" || true

mkdir -p /opt/eco-control-box
tar -xzf "$BOOT/eco_control_box.tar.gz" -C /opt/eco-control-box
/opt/eco-control-box/manual_control/remote/pi_setup/setup_box.sh "$PC_HOST" "${PC_PORT:-8791}"

# take the hook back out of cmdline.txt so this runs exactly once
sed -i 's| systemd.run=[^ ]*firstrun_eco.sh||; s| systemd.run_success_action=reboot||; s| systemd.unit=kernel-command-line.target||' "$BOOT/cmdline.txt"
rm -f "$BOOT/firstrun_eco.sh"
