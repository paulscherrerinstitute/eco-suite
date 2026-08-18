"""Apply a config bundle to a Delta Tau host: copy files + restart the IOC.

This ports the deployment mechanism of ``ConfigMotorIOC.py`` into a functional
API. The mechanism is deliberately unchanged because it is the *proven* way a
PowerBrick config is actually loaded:

1. copy the bundle files to ``/tmp`` on the host (``scp``),
2. optionally (re)configure ``shellbox`` when an ``ioc`` path is given,
3. restart the IOC over its telnet console and wait for the boot prompt.

.. warning::
   A wrong PowerBrick configuration can **destroy motors**. Applying is gated
   behind a safety password (type ``deltatau``) unless ``force=True``. Use
   ``dry_run=True`` to print the exact command sequence without touching a host.

Keyword-driven entry point
--------------------------
>>> from eco.motion.deltatau_config import apply
>>> apply(host="EXP1", config="stageXYZ", dry_run=True)   # bernina shortnames
>>> apply(host="XRD", config="kappa", force=True)          # real apply
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess as sprc
import sys
import time
from typing import Optional, Union

from .bundle import ConfigBundle, check_allowed, resolve_config, resolve_host

SAFETY_PASSWORD = "deltatau"

# Per device-family console credentials/prompts (ported from Config.run).
_CPPM = {
    "username": b"root",
    "password": b"deltatau",
    "prompt": b"ppmac# ",
    "iocPV": b"-CPPM-",
}


def device_profile(host: str) -> dict:
    """Return the telnet console credential/prompt profile for ``host``.

    ``-CPPM-`` hosts (PowerBrick) and ``-CSSU-``/``SSU`` hosts (soft IOC) use
    different prompts and passwords, exactly as in the original script.
    """
    if "-CPPM-" in host:
        return dict(_CPPM)
    if "-CSSU-" in host or host.startswith("SSU"):
        return {
            "username": b"root",
            "password": b"vi11igen310",
            "prompt": b"root@" + host.encode() + b":~# ",
            "iocPV": b"-CSSU-",
        }
    raise TypeError(f"unknown device type: {host}")


def _run(cmd, *, shell: bool, verbose: int, dry_run: bool) -> int:
    """Run (or, if ``dry_run``, only print) one subprocess command."""
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    if verbose & 1 or dry_run:
        print(("DRY-RUN " if dry_run else "") + printable)
    if dry_run:
        return 0
    return sprc.Popen(cmd, shell=shell).wait()


def _copy_config(host: str, bundle: ConfigBundle, *, verbose: int, dry_run: bool) -> None:
    """scp bundle files to ``/tmp`` on the host.

    Mirrors ``Config.copy_config``: the ``add_device`` startup file is copied to
    ``/tmp/add_device.cmd``; every entry in ``files`` is copied under its own name
    (``"*"`` copies the whole directory via a shell glob, exactly as the original).
    """
    if bundle.add_device:
        src = os.path.join(bundle.base, bundle.add_device)
        cmd = f"scp -r -o ConnectTimeout=3 {src} root@{host}:/tmp/add_device.cmd"
        if _run(cmd, shell=True, verbose=verbose, dry_run=dry_run):
            raise RuntimeError(f"scp failed: {cmd}")
    for entry in bundle.files:
        src = os.path.join(bundle.base, "*" if entry == "*" else entry)
        # non-"*" entries keep their sub-path under /tmp, like the original
        dst = "/tmp/" if entry == "*" else os.path.join("/tmp/", os.path.dirname(entry))
        cmd = f"scp -r -o ConnectTimeout=3 {src} root@{host}:{dst}"
        if _run(cmd, shell=True, verbose=verbose, dry_run=dry_run):
            raise RuntimeError(f"scp failed: {cmd}")


def _shellbox_cfg_run(host: str, ioc: str, port: int, *, verbose: int, dry_run: bool) -> None:
    """(Re)write ``/etc/shellbox.conf`` and restart shellbox (ported)."""
    cmds = [
        ("ssh", f"root@{host}", "touch", "/etc/shellbox.conf"),
        ("ssh", f"root@{host}", "sed", "/etc/shellbox.conf", "-e",
         f"s/^{port}/#{port}/g", "-i"),
        f"ssh root@{host} echo '{port} ioc {ioc} iocsh startup.script "
        f">> /etc/shellbox.conf'",
        ("ssh", f"root@{host}", "service", "shellbox", "stop"),
        ("ssh", f"root@{host}", "service", "shellbox", "start"),
    ]
    for cmd in cmds:
        if _run(cmd, shell=isinstance(cmd, str), verbose=verbose, dry_run=dry_run):
            raise RuntimeError(f"ssh failed: {cmd}")


def _ioc_restart_wait(host: str, port: int, profile: dict, ioc: Optional[str],
                      *, verbose: int, timeout_max: int = 20) -> int:
    """Restart the IOC over its telnet console and wait for the boot prompt.

    Ported from ``Config.ioc_restart_wait``; kept on ``telnetlib`` because the IOC
    console on ``port`` (default 50001) is a raw telnet service, distinct from the
    ``gpascii`` SSH channel used elsewhere.
    """
    import socket
    import telnetlib

    try:
        tp = telnetlib.Telnet(host, port, 3)
    except (socket.timeout, socket.error):
        print(f"cant telnet to {host}:{port}")
        return -1

    prompt = b"> "
    if ioc is None:  # toggle the ioc off/on via ctrl-t / ctrl-x
        tp.read_very_eager()
        while True:
            tp.write(b"\x14")  # ctrl-t
            tp.read_until(b"@@@ Toggled", 1)
            ch = tp.read_until(b"\n", 1)
            sys.stdout.write(ch[:-2].decode(errors="replace") + ", ")
            if ch.endswith(b"ON\r\n"):
                break
            tp.write(b"\x18")  # ctrl-x

    if verbose & 1:
        print("rebooting the ioc and waiting for prompt (~30 s)")
    s = b""
    timeout = 0
    while True:
        ch = tp.read_very_eager()
        timeout = timeout + 1 if ch == b"" else 0
        if verbose & 2:
            sys.stdout.write(ch.decode(errors="replace"))
        elif verbose & 1:
            sys.stdout.write("-" if timeout > 0 else ":")
        sys.stdout.flush()
        s += ch
        s = s[s[:-1].rfind(b"\n") + 1:]
        if timeout > timeout_max:
            break
        if s.endswith(prompt):
            m = re.match(rb"\w+" + profile["iocPV"] + rb"\w+> ", s)
            if m and m.end() == len(s):
                break
        time.sleep(1)

    tp.close()
    if timeout > timeout_max:
        print(f"\ntimed out on host {host}")
        return -1
    if verbose & 1:
        print(f"\nioc started with new configuration on host {host}")
    return 0


def apply(
    host: str,
    config: Union[str, ConfigBundle, tuple, dict, None] = None,
    *,
    ioc: Optional[str] = None,
    port: int = 50001,
    force: bool = False,
    verbose: int = 1,
    restart: bool = True,
    dry_run: bool = False,
) -> int:
    """Apply ``config`` to ``host`` (copy files + restart IOC).

    Parameters
    ----------
    host : str
        Short name (``"GPS"``/``"XRD"``/``"EXP1"``) or full hostname.
    config : str | ConfigBundle | tuple | dict | None
        Short config name (``"stageXYZ"`` ...), a :class:`ConfigBundle`, its dict
        form, or the legacy ``(base, add_device, *files)`` tuple. ``None`` only
        restarts the IOC.
    ioc : str, optional
        IOC path to (re)configure in ``shellbox`` before restart.
    force : bool
        Skip the interactive safety-password prompt.
    dry_run : bool
        Print the command sequence without executing anything.

    Returns ``0`` on success, non-zero on failure (never raises for the
    safety-password rejection — returns ``-1`` like the original CLI).
    """
    config_name = config if isinstance(config, str) else None
    full_host, _ = resolve_host(host)
    if config_name is not None:
        check_allowed(host, config_name)
    bundle = resolve_config(config) if config is not None else None

    if verbose & 1:
        print(f"host: {full_host}, config: {bundle.to_tuple() if bundle else None}")

    if not force and not dry_run:
        if getpass.getpass("input the safety password: ") != SAFETY_PASSWORD:
            print("wrong safety password")
            return -1

    profile = device_profile(full_host)
    if bundle is not None:
        _copy_config(full_host, bundle, verbose=verbose, dry_run=dry_run)
    if ioc is not None:
        _shellbox_cfg_run(full_host, ioc, port, verbose=verbose, dry_run=dry_run)

    if restart and not dry_run:
        return _ioc_restart_wait(full_host, port, profile, ioc, verbose=verbose)
    if restart and dry_run:
        print(f"DRY-RUN restart IOC on {full_host}:{port} and wait for prompt")
    return 0
