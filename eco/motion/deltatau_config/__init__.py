"""Object/functional interface to Delta Tau (PowerBrick / PPMAC) motor-IOC
configurations, refactored from the controls script
``/sf/bernina/config/src/python/ConfigMotorIOC/ConfigMotorIOC.py``.

What it gives you
-----------------
* **Read a config as a dict** and write it back losslessly
  (:func:`parse_cfg_file`, :class:`CfgFile`) — the ``.cfg`` template calls
  (``!motor(...)``, ``!encoder_inc(...)``) become per-axis argument dicts you can
  inspect and edit.
* **Model a config bundle** (:class:`ConfigBundle`) with ``to_dict``/``from_dict``
  and the bernina host/config registries (:data:`HOSTS`, :data:`CONFIGS`), so the
  original CLI shortnames are reachable as keyword arguments.
* **Apply a config to a host** (:func:`apply`) — copy files + restart the IOC,
  behind a safety-password gate, with a ``dry_run`` mode.
* **Verify the live config** (:func:`read_live`, :func:`check`, :func:`diff`) — read
  the present configuration off the running device and compare it with what a
  bundle expects.

Quick start
-----------
>>> from eco.motion.deltatau_config import parse_cfg_file, CONFIGS, apply, check
>>> cfg = parse_cfg_file(CONFIGS["stageXYZ"].cfg_files()[0])
>>> cfg.to_dict()["axes"][1]["motor"]["JogSpeed"]
1.0
>>> apply(host="EXP1", config="stageXYZ", dry_run=True)   # inspect commands
>>> check("EXP1", "stageXYZ")                              # verify live device

.. warning::
   Applying a wrong PowerBrick config can **destroy motors**. :func:`apply` asks
   for the ``deltatau`` safety password unless ``force=True``.

Eventual bernina_diffractometers integration (design sketch, not yet wired)
---------------------------------------------------------------------------
The diffractometer assemblies in
``eco/endstations/bernina_diffractometers.py`` know which PowerBrick host each
stage lives on (e.g. ``SARES22-GPS``, ``SARES21-XRD``). At startup they could
call :func:`check(host, CONFIGS[...])` to confirm the device is configured as
expected and, if not, offer to :func:`apply` the known-good bundle. That wiring
is intentionally left as a reviewed follow-up given the motor-damage risk.
"""

from .bundle import (
    CONFIGS,
    HOSTS,
    ConfigBundle,
    resolve_config,
    resolve_host,
)
from .cfgfile import CfgFile, parse_cfg, parse_cfg_file
from .deploy import apply, device_profile
from .verify import check, diff, expected_from_config, read_live

__all__ = [
    "ConfigBundle",
    "HOSTS",
    "CONFIGS",
    "resolve_host",
    "resolve_config",
    "CfgFile",
    "parse_cfg",
    "parse_cfg_file",
    "apply",
    "device_profile",
    "read_live",
    "diff",
    "check",
    "expected_from_config",
]
