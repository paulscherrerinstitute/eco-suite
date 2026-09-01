"""Show this box's MAC addresses / IPs / hostname on its own touchscreen.

Solves the bring-up chicken-and-egg: a new box cannot be reached over the
network until its Ethernet MAC is registered, and the MAC cannot be read
over the network. So the box displays it, and also writes it to the FAT
boot partition (readable by putting the card in any laptop).

Runs on stock Raspberry Pi OS with desktop - stdlib + Tkinter only, no eco,
no extra packages. Started from /etc/xdg/autostart/eco-netinfo.desktop so
it works under both X11 and Wayland sessions.

    python3 show_netinfo.py            # window if a display is available
    python3 show_netinfo.py --print    # plain text, for an ssh session
"""

import os
import subprocess
import sys

BOOT_FILES = ("/boot/firmware/network_info.txt", "/boot/network_info.txt")
AUTOSTART = "/etc/xdg/autostart/eco-netinfo.desktop"
SYS_NET = "/sys/class/net"


def _read(path, default=""):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def pi_serial():
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("Serial"):
            return line.split(":")[-1].strip()
    return "unknown"


def ipv4_addresses():
    """{interface: 'a.b.c.d/nn'} from iproute2 (always present)."""
    out = {}
    try:
        text = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                              capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) > 3:
            out.setdefault(parts[1], parts[3])
    return out


def interfaces():
    """[(name, mac, state, ip)] for every real interface, wired first."""
    ips = ipv4_addresses()
    rows = []
    for name in sorted(os.listdir(SYS_NET)):
        if name == "lo":
            continue
        rows.append((
            name,
            _read(f"{SYS_NET}/{name}/address", "??:??:??:??:??:??"),
            _read(f"{SYS_NET}/{name}/operstate", "unknown"),
            ips.get(name, "-"),
        ))
    rows.sort(key=lambda r: (not r[0].startswith("e"), r[0]))  # eth* first
    return rows


def report():
    lines = [
        f"hostname : {os.uname().nodename}",
        f"Pi serial: {pi_serial()}",
        "",
        f"{'interface':<10}{'MAC address':<20}{'state':<10}IPv4",
    ]
    for name, mac, state, ip in interfaces():
        lines.append(f"{name:<10}{mac:<20}{state:<10}{ip}")
    return "\n".join(lines)


def wired_mac():
    for name, mac, _, _ in interfaces():
        if name.startswith("e"):
            return mac
    return None


def save_to_boot(text):
    """Write the report where a laptop can read it off the SD card."""
    for path in BOOT_FILES:
        if not os.path.isdir(os.path.dirname(path)):
            continue
        try:
            with open(path, "w") as fh:
                fh.write(text + "\n")
            return path
        except PermissionError:
            # not root: Raspberry Pi OS gives the first user passwordless sudo
            try:
                subprocess.run(["sudo", "-n", "tee", path], input=text + "\n",
                               text=True, capture_output=True, timeout=5, check=True)
                return path
            except (OSError, subprocess.SubprocessError):
                continue
        except OSError:
            continue
    return None


def dismiss():
    """Stop showing this at every boot (once the box is registered)."""
    for cmd in (["rm", "-f", AUTOSTART], ["sudo", "-n", "rm", "-f", AUTOSTART]):
        try:
            if subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def gui():
    import tkinter as tk

    BG, FG, ACCENT, MUTED = "#101010", "#eeeeee", "#4da3ff", "#888888"
    root = tk.Tk()
    root.title("eco control box - network info")
    root.configure(bg=BG)
    root.attributes("-fullscreen", True)

    tk.Label(root, text="Register this box on the network", fg=ACCENT, bg=BG,
             font=("Helvetica", 20, "bold")).pack(pady=(14, 2))
    mac = wired_mac()
    tk.Label(root, text=f"Ethernet MAC:  {mac or 'no wired interface found'}",
             fg="#ffcc55", bg=BG, font=("DejaVu Sans Mono", 22, "bold")).pack(pady=4)

    body = tk.Label(root, text=report(), fg=FG, bg=BG, justify="left",
                    font=("DejaVu Sans Mono", 12))
    body.pack(padx=16, pady=8, anchor="w")

    saved = save_to_boot(report())
    tk.Label(root, fg=MUTED, bg=BG, font=("Helvetica", 10), justify="left",
             text=("also written to " + saved if saved else
                   "could not write to the boot partition")).pack()

    row = tk.Frame(root, bg=BG)
    row.pack(side="bottom", pady=12)

    def refresh():
        body.config(text=report())
        root.after(2000, refresh)

    def dismiss_and_quit():
        dismiss()
        root.destroy()

    tk.Button(row, text="Close", font=("Helvetica", 13), command=root.destroy).pack(side="left", padx=8)
    tk.Button(row, text="Registered - don't show again", font=("Helvetica", 13),
              command=dismiss_and_quit).pack(side="left", padx=8)
    root.bind("<Escape>", lambda e: root.destroy())
    root.after(2000, refresh)
    root.mainloop()


def main():
    text = report()
    saved = save_to_boot(text)
    headless = "--print" in sys.argv or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if headless:
        print(text)
        print("\nwritten to " + saved if saved else "\n(boot partition not writable)")
        return
    gui()


if __name__ == "__main__":
    main()
