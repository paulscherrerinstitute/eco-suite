#!/bin/bash
# Provision the PSI "Motor Control Unit" box (Pi 3B + 7" touchscreen +
# MCP3008 joystick + KY-040 encoder, Ethernet/PoE) as an eco control box.
#
# Run ON THE PI, from the directory that contains the copied bundle:
#     sudo ./manual_control/remote/pi_setup/setup_box.sh <pc-host> [port]
#
# It is idempotent - re-run it after copying a newer bundle.
set -euo pipefail

PC_HOST="${1:-}"
PC_PORT="${2:-8791}"
DEST="${ECO_DEST_DIR:-/opt/eco-control-box}"
REHEARSE="${ECO_REHEARSE:-0}"   # test hook: skip the privileged/network steps
ETC="${ECO_ETC_DIR:-/etc}"      # test hook: where the config/token land
# Who the GUI runs as: the invoking user under sudo, else the first real
# login account (the box's user is whatever was set in Raspberry Pi Imager -
# not necessarily "pi").
RUN_USER="${SUDO_USER:-}"
if [[ -z "$RUN_USER" ]] || ! id -u "$RUN_USER" >/dev/null 2>&1; then
    RUN_USER=$(awk -F: '$3>=1000 && $3<65000 {print $1; exit}' /etc/passwd)
fi
: "${RUN_USER:=root}"

if [[ -z "$PC_HOST" ]]; then
    echo "usage: sudo $0 <pc-host-running-eco> [port]" >&2
    exit 2
fi
if [[ $EUID -ne 0 && "$REHEARSE" != "1" ]]; then
    echo "run with sudo" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # .../manual_control/remote/pi_setup
BUNDLE="$(cd "$HERE/../.." && pwd)"                        # .../manual_control
if [[ ! -f "$BUNDLE/remote/pi_app.py" ]]; then
    echo "cannot find the manual_control bundle next to this script" >&2
    exit 1
fi

# python3-lgpio is NOT optional: gpiozero 2.x has no built-in pin factory and
# dies with "Unable to load any default pin factory!" without one, which kills
# the encoder and every GPIO button on the box.
PKGS=(python3-tk python3-spidev python3-gpiozero python3-lgpio)

if [[ "$REHEARSE" != "1" ]]; then
    # The Pi has no RTC. A clock that is days off makes apt reject every
    # repository signature ("Not live until ..."), so fix the time first.
    echo "== clock =="
    if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != "yes" ]]; then
        echo "clock not synchronised ($(date -Is)); pointing timesyncd at ${ECO_NTP_SERVERS:-the PSI time servers}"
        mkdir -p /etc/systemd/timesyncd.conf.d
        cat > /etc/systemd/timesyncd.conf.d/10-eco.conf <<NTP
[Time]
NTP=${ECO_NTP_SERVERS:-pstime1.psi.ch pstime2.psi.ch pstime3.psi.ch}
NTP
        timedatectl set-ntp true || true
        systemctl restart systemd-timesyncd || true
        for _ in $(seq 10); do
            [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "yes" ]] && break
            sleep 1
        done
        echo "clock now: $(date -Is) (synchronised: $(timedatectl show -p NTPSynchronized --value 2>/dev/null))"
    fi

    echo "== packages (Python 3 + Tk + SPI + GPIO; no eco, no EPICS) =="
    # Non-fatal: on a box whose clock was wrong, apt indexes may be stale but
    # the packages are usually present already. Only a genuinely missing
    # package is fatal.
    apt-get update || echo "warning: apt-get update failed (stale indexes will be used)"
    apt-get install -y "${PKGS[@]}" || echo "warning: apt-get install failed"
    missing=()
    for pkg in "${PKGS[@]}"; do
        dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR: still missing: ${missing[*]} - fix networking/time, then re-run" >&2
        exit 1
    fi

    echo "== enable SPI (the MCP3008 joystick ADC hangs off SPI0) =="
    if command -v raspi-config >/dev/null; then
        raspi-config nonint do_spi 0
    else
        grep -q '^dtparam=spi=on' /boot/firmware/config.txt 2>/dev/null \
            || echo 'dtparam=spi=on' >> /boot/firmware/config.txt
    fi
fi

echo "== install the bundle to $DEST =="
mkdir -p "$DEST"
# The bundle may already BE $DEST/manual_control (that is what the documented
# "tar -xzf ... -C /opt/eco-control-box" does). Copying it onto itself after
# an rm -rf would delete it, so detect that and install in place.
if [[ "$(realpath "$BUNDLE")" == "$(realpath -m "$DEST/manual_control")" ]]; then
    echo "bundle is already at $DEST/manual_control - installing in place"
else
    rm -rf "$DEST/manual_control"
    cp -r "$BUNDLE" "$DEST/manual_control"
fi
chown -R "$RUN_USER" "$DEST" || echo "warning: could not chown $DEST to $RUN_USER"

echo "== hardware access for $RUN_USER =="
# The service runs as this user; without these groups it cannot open
# /dev/spidev* (joystick) or the GPIO character device (encoder).
for grp in gpio spi i2c; do
    getent group "$grp" >/dev/null || continue
    id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx "$grp" \
        || usermod -aG "$grp" "$RUN_USER" && echo "  $RUN_USER in group $grp"
done

echo "== link config =="
cat > "$ETC/eco-control-box.env" <<ENV
PC_HOST=$PC_HOST
PC_PORT=$PC_PORT
ENV
if [[ ! -f "$ETC/eco-control-box.token" ]]; then
    head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$ETC/eco-control-box.token"
    echo "generated a new shared token"
fi
chmod 640 "$ETC/eco-control-box.token"
chown root:"$(id -gn "$RUN_USER")" "$ETC/eco-control-box.token" 2>/dev/null || true

echo "== autostart =="
[[ "$REHEARSE" == "1" ]] && { echo "(rehearsal: stopping before systemd)"; echo "token: $(cat "$ETC/eco-control-box.token" 2>/dev/null || echo n/a)"; exit 0; }
sed "s/^User=pi$/User=$RUN_USER/; s#/home/pi/.Xauthority#/home/$RUN_USER/.Xauthority#" \
    "$HERE/eco-control-box.service" > /etc/systemd/system/eco-control-box.service
systemctl daemon-reload
systemctl enable eco-control-box

cat <<DONE

Done. Token (put the SAME string on the PC side):

    $(cat "$ETC/eco-control-box.token")

On the PC, inside or beside your eco session:

    python -m eco.manual_control.remote.serve --bernina --tcp $PC_PORT \\
        --bind 0.0.0.0 --token '$(cat "$ETC/eco-control-box.token")'

Then on the box:  sudo systemctl start eco-control-box
Logs:             journalctl -u eco-control-box -f
A reboot is needed once if SPI was just enabled.
DONE
