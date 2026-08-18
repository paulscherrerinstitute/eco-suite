"""Resolve which IOC (Channel Access server host:port) currently serves a PV.

This reimplements, in-process via pyepics, the one piece of `cainfo`/`pvinfo`
output that is actually useful for tracking down hardware: the "Host:" line,
i.e. the address of the process that answered the CA search for a PV name.

Read this before assuming it "reaches" a device
--------------------------------------------------
This only ever resolves the **IOC's own CA server address** -- exactly what
``cainfo <pv>`` prints as ``Host:`` and what ``pvinfo -p ca <pv>`` reports.
For a PSI Bernina motion PV such as ``SARES20-MF2:MOT_1`` that is the host
running the IOC (soft IOC or IOC crate), *not* the IP:port of any downstream
serial-to-Ethernet gateway/terminal server (moxa/digi) that the IOC's
asyn/StreamDevice layer may use internally to reach a Schneider MForce/MDrive
drive over RS-485. That mapping is configured only inside the IOC's own
startup script (``st.cmd``, ``asynSetOption``/``drvAsynIPPortConfigure``/
``drvAsynSerialPortConfigure`` calls) and is invisible to any Channel Access
or pvAccess client -- ``cainfo``, ``pvinfo`` and this module included.

So: useful for quickly finding *which host to log into* to go read that
IOC's ``st.cmd`` and find the real gateway address by hand. Not useful for
skipping that step.
"""

from __future__ import annotations

from dataclasses import dataclass

from epics import PV


@dataclass
class PvLocation:
    pvname: str
    connected: bool
    host: str | None = None
    port: int | None = None
    native_type: str | None = None
    count: int | None = None
    access: str | None = None

    def __str__(self) -> str:
        if not self.connected:
            return f"{self.pvname}: not connected"
        return (
            f"{self.pvname} @ {self.host}:{self.port} "
            f"(type={self.native_type}, count={self.count}, access={self.access})"
        )


def locate(pvname: str, timeout: float = 2.0) -> PvLocation:
    """Resolve the CA server host:port currently answering for *pvname*.

    Equivalent to the "Host:" line of ``cainfo <pvname>`` / ``pvinfo -p ca
    <pvname>``, done in-process instead of shelling out. Returns a
    :class:`PvLocation` with ``connected=False`` if no IOC answers within
    *timeout* seconds (e.g. no network route to the control-system segment).
    """
    pv = PV(pvname, connection_timeout=timeout, auto_monitor=False)
    connected = pv.wait_for_connection(timeout=timeout)
    if not connected:
        return PvLocation(pvname=pvname, connected=False)
    host, _, port = pv.host.rpartition(":")
    return PvLocation(
        pvname=pvname,
        connected=True,
        host=host or pv.host,
        port=int(port) if port.isdigit() else None,
        native_type=pv.type,
        count=pv.count,
        access=pv.access,
    )


def locate_many(pvnames, timeout: float = 2.0) -> dict[str, PvLocation]:
    """Resolve several PVs at once; returns ``{pvname: PvLocation}``."""
    return {name: locate(name, timeout=timeout) for name in pvnames}


def locate_mforce_channel(pv_controller: str, channel: str | int) -> dict[str, PvLocation]:
    """Resolve the IOC serving one Bernina MForce channel's PVs.

    Covers the motor record (``MOT_<channel>``) and the raw MCode
    passthrough PVs (``<channel>_RC``, ``<channel>_set``, ``<channel>_get``).
    They are normally all served by the same IOC, so this is mainly a sanity
    check -- it cannot resolve the serial gateway address; see the module
    docstring.
    """
    base = f"{pv_controller}:"
    pvnames = {
        "motor_record": base + f"MOT_{channel}",
        "run_current": base + f"{channel}_RC",
        "mcode_set": base + f"{channel}_set",
        "mcode_get": base + f"{channel}_get",
    }
    return {key: locate(pvname) for key, pvname in pvnames.items()}


def _main() -> None:  # pragma: no cover - manual bench utility
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m eco.epics.pv_locate",
        description="Resolve the IOC (CA server host:port) serving one or more PVs.",
    )
    p.add_argument("pvname", nargs="+")
    p.add_argument("-w", "--timeout", type=float, default=2.0)
    args = p.parse_args()

    for name in args.pvname:
        print(locate(name, timeout=args.timeout))


if __name__ == "__main__":  # pragma: no cover
    _main()
