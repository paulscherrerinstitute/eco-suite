#!/bin/bash
# Safely arm (or disarm) the first-boot hook in a Raspberry Pi cmdline.txt.
#
#   ./arm_firstrun.sh /path/to/bootfs            # arm
#   ./arm_firstrun.sh /path/to/bootfs --disarm   # remove it again
#
# Editing cmdline.txt by hand is error-prone in exactly two ways, both of
# which leave the Pi dropping into rescue mode with
#   "systemd[1]: Failed to load default target: No such file or directory"
#   - the parameters end up on a SECOND line (the kernel reads only the first)
#   - the editor saves CRLF, so the last parameter carries a stray \r
# This script normalises the file to one LF-terminated line, is idempotent,
# and keeps a backup.
set -euo pipefail

BOOTFS="${1:-}"
MODE="${2:-arm}"
HOOK="systemd.run=/boot/firmware/firstrun_eco.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"

if [[ -z "$BOOTFS" || ! -f "$BOOTFS/cmdline.txt" ]]; then
    echo "usage: $0 /path/to/bootfs [--disarm]   (the FAT partition holding cmdline.txt)" >&2
    exit 2
fi
[[ -f "$BOOTFS/firstrun_eco.sh" || "$MODE" == "--disarm" ]] || {
    echo "warning: $BOOTFS/firstrun_eco.sh is not there - copy the payload first" >&2
    exit 1
}

cp -n "$BOOTFS/cmdline.txt" "$BOOTFS/cmdline.txt.eco-backup" 2>/dev/null || true

# one line, no CR, no duplicate hook parameters, single spaces
line=$(tr -d '\r' < "$BOOTFS/cmdline.txt" | tr '\n' ' ')
for param in 'systemd.run=[^ ]*firstrun_eco.sh' 'systemd.run_success_action=reboot' \
             'systemd.unit=kernel-command-line.target'; do
    line=$(sed -E "s#(^| )$param##g" <<<"$line")
done
line=$(sed -E 's/  +/ /g; s/^ //; s/ $//' <<<"$line")

if [[ "$MODE" == "--disarm" ]]; then
    # Raspberry Pi Imager's OWN first-boot hook (firstrun.sh, which creates
    # the user and enables SSH) shares the two generic parameters we just
    # stripped. If it is still pending, put them back or its customisation
    # silently never runs.
    if grep -q 'systemd.run=' <<<"$line"; then
        line="$line systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
        echo "note: kept the Imager first-boot hook intact"
    fi
else
    line="$line $HOOK"
fi

printf '%s\n' "$line" > "$BOOTFS/cmdline.txt"
sync    # FAT writes sit in the page cache: without this (and a proper
        # eject/unmount) the card can still hold the OLD file when the Pi
        # reads it, so the edit "succeeds" and changes nothing.

echo "cmdline.txt is now ($(wc -l < "$BOOTFS/cmdline.txt") line, $(wc -c < "$BOOTFS/cmdline.txt") bytes):"
echo
cat "$BOOTFS/cmdline.txt"
echo
grep -q $'\r' "$BOOTFS/cmdline.txt" && echo "ERROR: still contains CR" && exit 1
[[ $(wc -l < "$BOOTFS/cmdline.txt") -eq 1 ]] || { echo "ERROR: not a single line"; exit 1; }
echo "OK - single LF-terminated line, backup in cmdline.txt.eco-backup"
echo "NOW UNMOUNT/EJECT THE CARD PROPERLY before pulling it out."
