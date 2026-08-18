"""The *config bundle* — which files make up one Delta Tau IOC configuration —
and the bernina host / config registries ported from the original CLI lookup
tables.

In ``ConfigMotorIOC.py`` a configuration is a bare tuple
``(basepath, add_device.cmd | '' | None, *files)`` and hosts/configs are chosen
through two hard-coded dicts (``hostLUT``, ``cfgFnLUT``) plus example CLI strings.
Here the tuple becomes a small :class:`ConfigBundle` object with ``to_dict`` /
``from_dict``, and the lookup tables become the module-level :data:`HOSTS` /
:data:`CONFIGS` registries with explicit resolver functions — so the bernina CLI
shortnames (``GPS``, ``XRD``, ``EXP1`` / ``stage``, ``kappa``, ``stageXYZ`` ...)
are reachable as ordinary keyword arguments to :func:`~.deploy.apply`.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

# Directory that ships the bernina example config folders (stageXYZ/, europium/,
# PowerBrickExample/, ...). In the original this was ``os.path.dirname(__file__)``
# of the CLI script; keep pointing at that canonical config checkout.
CONFIG_ROOT = "/sf/bernina/config/src/python/ConfigMotorIOC"

# Where the shared GPS/XRD IOC startup ``.cmd`` files live on the IOC host tree.
IOC_ESB_GPS_XRD = "/ioc/modules/ESB_GPS_XRD/0.0.2/R7.0.7/"


@dataclass
class ConfigBundle:
    """The set of files that make up one IOC configuration.

    Attributes
    ----------
    base : str
        Local base directory containing the files.
    add_device : str | None
        The startup ``.cmd`` copied to the host as ``/tmp/add_device.cmd``.
        Empty string / ``None`` means "the directory already contains a plain
        ``add_device.cmd``, copy it as part of ``files``".
    files : list[str]
        Additional files to copy. The literal ``"*"`` means "the whole directory".
    """

    base: str
    add_device: Optional[str] = None
    files: List[str] = field(default_factory=list)

    # --- dict round-trip --------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"base": self.base, "add_device": self.add_device, "files": list(self.files)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConfigBundle":
        return cls(
            base=d["base"],
            add_device=d.get("add_device"),
            files=list(d.get("files", [])),
        )

    # --- legacy tuple interop --------------------------------------------
    @classmethod
    def from_tuple(cls, tpl: Tuple) -> "ConfigBundle":
        """Build from the original ``(base, add_device, *files)`` tuple form."""
        base = tpl[0]
        add_device = tpl[1] if len(tpl) > 1 else None
        files = list(tpl[2:])
        return cls(base=base, add_device=add_device or None, files=files)

    def to_tuple(self) -> Tuple:
        return (self.base, self.add_device or "", *self.files)

    # --- file resolution --------------------------------------------------
    def resolve_files(self) -> List[str]:
        """Expand ``"*"`` and the ``add_device`` entry to concrete local paths.

        Mirrors the file iteration in the original ``Config.copy_config`` but
        returns paths instead of running ``scp``, so it can be inspected / tested
        offline and re-used by :func:`~.deploy.apply`.
        """
        out: List[str] = []
        if self.add_device:
            out.append(os.path.join(self.base, self.add_device))
        for entry in self.files:
            if entry == "*":
                out.extend(sorted(glob.glob(os.path.join(self.base, "*"))))
            else:
                out.append(os.path.join(self.base, entry))
        return out

    def cfg_files(self) -> List[str]:
        """The ``.cfg`` PowerBrick config file(s) in this bundle (for parsing)."""
        return [p for p in self.resolve_files() if p.endswith(".cfg")]


# --- bernina registries (ported from ConfigMotorIOC.py LUTs) --------------

# short host name -> (full hostname, allowed config names or "*")
HOSTS: Dict[str, Tuple[str, Union[str, Tuple[str, ...]]]] = {
    "GPS": ("SARES22-CPPM-GPS1", ("stage", "kappa")),
    "XRD": ("SARES21-CPPM-XRD1", ("stage", "kappa")),
    "EXP1": ("SARES20-CPPM-EXP1", "*"),
}

# short config name -> ConfigBundle
CONFIGS: Dict[str, ConfigBundle] = {
    "stage": ConfigBundle(IOC_ESB_GPS_XRD, "add_deviceSampleStage.cmd"),
    "kappa": ConfigBundle(IOC_ESB_GPS_XRD, "add_deviceKappa.cmd"),
    "stageXYZ": ConfigBundle(
        os.path.join(CONFIG_ROOT, "stageXYZ"), "add_deviceStageXYZ.cmd", ["*"]
    ),
    "europium": ConfigBundle(os.path.join(CONFIG_ROOT, "europium"), None, ["*"]),
    "PowerBrickExample": ConfigBundle(
        os.path.join(CONFIG_ROOT, "PowerBrickExample"), None, ["*"]
    ),
    "MForceExample": ConfigBundle(
        os.path.join(CONFIG_ROOT, "MForceExample"), None, ["*"]
    ),
}


def resolve_host(host: str) -> Tuple[str, Union[str, Tuple[str, ...]]]:
    """Resolve a short host name to ``(full_hostname, allowed_configs)``.

    Accepts a registry shortname (``"GPS"``) or a full hostname (returned as-is
    with ``"*"`` allowed).
    """
    if host in HOSTS:
        return HOSTS[host]
    return host, "*"


def resolve_config(config: Union[str, ConfigBundle, Tuple, Dict]) -> ConfigBundle:
    """Resolve a config given as a shortname, :class:`ConfigBundle`, dict or the
    legacy ``(base, add_device, *files)`` tuple."""
    if isinstance(config, ConfigBundle):
        return config
    if isinstance(config, dict):
        return ConfigBundle.from_dict(config)
    if isinstance(config, (tuple, list)):
        return ConfigBundle.from_tuple(tuple(config))
    if isinstance(config, str):
        if config in CONFIGS:
            return CONFIGS[config]
        raise KeyError(
            f"unknown config {config!r}; known: {sorted(CONFIGS)} "
            "or pass a ConfigBundle / (base, add_device, *files) tuple"
        )
    raise TypeError(f"cannot resolve config from {config!r}")


def check_allowed(host: str, config_name: Optional[str]) -> None:
    """Raise ``ValueError`` if ``config_name`` is not allowed on ``host`` per the
    registry (mirrors the original 'not allowed config for that host!' guard)."""
    _, allowed = resolve_host(host)
    if allowed == "*" or config_name is None:
        return
    if config_name not in allowed:
        raise ValueError(
            f"config {config_name!r} not allowed on host {host!r}; allowed: {allowed}"
        )
