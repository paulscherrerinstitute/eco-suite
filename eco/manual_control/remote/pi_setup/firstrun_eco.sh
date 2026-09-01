#!/bin/bash
# First-boot hook for the eco control box. Runs ONCE as root, very early,
# before the desktop, via a systemd.run= entry in cmdline.txt (see below).
#
# It is deliberately ordered so the useful part happens even when the box
# has no working network yet - which is the normal case for a brand-new
# box whose MAC is not registered:
#   1. write the MAC/IP report to the boot partition   (always works)
#   2. install the on-screen network-info display      (always works)
#   3. unpack the eco thin client                      (always works)
#   4. try setup_box.sh - needs apt, so it may fail    (tolerated)
# A failure in step 4 is recorded and skipped: re-run setup_box.sh over
# ssh once the box is registered.
#
# Install: put this file, eco_control_box.tar.gz and eco_pc_host on the
# card's BOOT partition (the small FAT one), then append to the END of the
# single line in cmdline.txt:
#
#   systemd.run=/boot/firmware/firstrun_eco.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
#
# (older images: /boot/firstrun_eco.sh)
set -u

# ECO_BOOT_DIR / ECO_DEST_DIR exist so this can be rehearsed off-device.
BOOT="${ECO_BOOT_DIR:-/boot/firmware}"
[ -d "$BOOT" ] || BOOT=/boot
LOG="$BOOT/eco_firstrun.log"
DEST="${ECO_DEST_DIR:-/opt/eco-control-box}"
AUTOSTART_DIR="${ECO_AUTOSTART_DIR:-/etc/xdg/autostart}"
exec >>"$LOG" 2>&1
echo "=== eco firstrun $(date -Is) ==="
set -x

mount -o remount,rw "$BOOT" 2>/dev/null || true

# 1. the MAC, where a laptop can read it off the card
{
    echo "hostname : $(hostname)"
    echo ""
    printf '%-10s%-20s%-10s%s\n' interface "MAC address" state IPv4
    for dev in /sys/class/net/*; do
        name=$(basename "$dev")
        [ "$name" = lo ] && continue
        ip4=$(ip -o -4 addr show "$name" 2>/dev/null | awk '{print $4; exit}')
        printf '%-10s%-20s%-10s%s\n' "$name" "$(cat "$dev/address")" \
            "$(cat "$dev/operstate")" "${ip4:--}"
    done
} > "$BOOT/network_info.txt"

# 3. unpack first, because step 2's autostart entry points into it
mkdir -p "$DEST"
tar -xzf "$BOOT/eco_control_box.tar.gz" -C "$DEST"

# 2. show the same report on the touchscreen at every boot until dismissed
install -m 644 "$DEST/manual_control/remote/pi_setup/eco-netinfo.desktop" \
    "$AUTOSTART_DIR/eco-netinfo.desktop" || true

# 4. full install - needs apt, i.e. a working (registered) network
PC_HOST=""; PC_PORT=8791
if [ -r "$BOOT/eco_pc_host" ]; then
    read -r PC_HOST PC_PORT _ < "$BOOT/eco_pc_host" || true
fi
if [ -n "$PC_HOST" ] && ping -c1 -W3 "$PC_HOST" >/dev/null 2>&1; then
    if "$DEST/manual_control/remote/pi_setup/setup_box.sh" "$PC_HOST" "${PC_PORT:-8791}"; then
        echo "INSTALL OK" > "$BOOT/eco_install_status.txt"
    else
        echo "INSTALL FAILED - re-run setup_box.sh over ssh, see eco_firstrun.log" \
            > "$BOOT/eco_install_status.txt"
    fi
else
    echo "SKIPPED - no network to '$PC_HOST' yet (register the MAC in network_info.txt, then re-run setup_box.sh over ssh)" \
        > "$BOOT/eco_install_status.txt"
fi

# always disarm the hook, whatever happened above, so this never boot-loops
sed -i 's| systemd.run=[^ ]*firstrun_eco.sh||; s| systemd.run_success_action=reboot||; s| systemd.unit=kernel-command-line.target||' "$BOOT/cmdline.txt"
rm -f "$BOOT/firstrun_eco.sh"
sync
echo "=== eco firstrun done $(date -Is) ==="
exit 0
