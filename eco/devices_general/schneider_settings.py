"""Schneider/IMS MForce & MDrive motor-controller settings sub-assembly.

Appended to a :class:`~eco.devices_general.motors.MotorRecord` via its
``schneider=`` keyword (mirrors ``pb_conf``/``PowerBrickChannelPars`` for
PowerBrick controllers, see :mod:`eco.devices_general.powerbrick`). Treats
MForce and MDrive as equivalent -- both speak the same MCode command set
(see :mod:`eco.devices_general.schneider_mcode` and its
``..._presets`` companion).

Why this still goes through EPICS, not schneider_mcode's direct serial driver
-------------------------------------------------------------------------------
:mod:`eco.devices_general.schneider_mcode` (``SchneiderBus``/``SchneiderMotor``)
talks MCode directly over a serial port -- useful on a bench, but **not
safe** to point at the live SARES20-MF1/MF2 channels, and not just for the
generic "don't fight the IOC for the bus" reason. Confirmed from the actual
IOC boot area (``/ioc/SARES20-CSSU-MF1/startup.script_SARES20-CSSU-MF1``,
readable directly -- this host has ``/ioc`` NFS-mounted): each of the 16
channels gets its own ``drvAsynSerialPortConfigure("asynPortN",
"/dev/ttyMN", ...)`` -- one dedicated local tty per motor on the IOC's own
Moxa DA-662A-16-LX embedded box, not a shared party-mode bus, and not
reachable over the network at all. This module's ``_get``/``_set``/``_RC``/
``_HC`` PVs are the *only* channel to the drive that exists from off-box --
there is no independent path, safe or otherwise, without SSH+root on that
embedded box (no credentials for it exist in this environment's
``~/.ssh/config`` either way). So this module reads/writes through the
IOC's own passthrough PVs -- broader parameter coverage and derived
convenience (deadband) on top, not a competing connection.

If you do have a Schneider drive on a genuinely dedicated, non-IOC-owned
serial port (bench setup, spare channel), use
:mod:`eco.devices_general.schneider_mcode` directly instead -- that's what
it's for. Nothing stops you from doing so; it just isn't wired into this
module or any `Assembly`/`get_status()` flow, so using it is a deliberate,
manual, standalone action, not something that could happen incidentally.

The EPICS interface, confirmed from source
----------------------------------------------
Found the actual record/protocol definitions (same NFS-mounted `/ioc` boot
area): ``/ioc/SARES20-CSSU-MF1/MDrive.template`` (record defs) and
``/ioc/SARES20-CSSU-MF1/cfg/schneider.cfg`` (the StreamDevice protocol).
This replaces the empirical guessing from earlier sessions with the actual
source:

* ``$(P):$(ID)_set`` is a ``stringout``, ``$(P):$(ID)_get`` a ``stringin``
  -- both hard-capped at EPICS's ``MAX_STRING_SIZE`` (40 chars); the
  template's own comment on ``_set`` even says so ("is limited to 40
  characters").
* The StreamDevice ``get`` protocol is::

      get { out "1pr %s"; in "%#s" }

  i.e. send ``1pr <mnemonic>`` (``1`` = the drive's fixed party-mode-style
  device address on its dedicated line) and capture *one* reply into the
  40-char string. ``_get`` is genuinely bidirectional as found empirically
  (write the bare mnemonic, the same PV holds the reply after the drive
  answers) -- so reading, not just writing, *does* work through the
  existing interface for anything that fits on one line. Confirmed live
  for `VR`/`PN`/`SN`/`MS`/`EF`/`ER`/`RC`/`DB`/`IS`.
* But `in "%#s"` captures a single reply unit, not a multi-line one -- when
  the drive's actual reply spans multiple lines (`PR IS`, one line per
  configured wire), only the first line survives. This is now a confirmed
  protocol/record limitation, not a guess about mnemonic syntax: there is
  no way to reach wire 2's line through this interface as deployed. See
  :meth:`SchneiderMotorSettings.describe_input_assignment`.
* `_RC`/`_HC` are ``longout``-only (`set_int`, no `get_int`/INP defined) --
  reading them returns the IOC's last-*commanded* value, not a fresh
  live query of the drive's actual current setting.
* One record (`_VR`, firmware version) already uses a dedicated
  `get_str(VR)` protocol function instead of the generic `get` -- showing
  the pattern for a properly multi-line-aware record exists in principle
  (e.g. a StreamDevice function reading until a real end marker, into a
  waveform/char-array record instead of `stringin`) -- just not written for
  `IS`. That would require redeploying the IOC database; out of scope here.

Host/console info is informational only
------------------------------------------
:meth:`SchneiderMotorSettings.host_info`/:meth:`console_command` resolve the
owning IOC's host and console port via :func:`eco.epics_utils.iocinfo.find_ioc`,
for display/telnet-console purposes (see
:mod:`eco.widgets.ioc_finder_widget`/``_qt``). This class never opens a
network connection to that host itself.
"""

from __future__ import annotations

import threading
import time

from ..elements.assembly import Assembly
from ..elements.adjustable import AdjustableGetSet
from ..elements.detector import DetectorGet
from ..epics_utils.adjustable import AdjustablePv
from .schneider_mcode_presets import MCODE_PARAMETERS

# MCode variables whose `PR <mnemonic>` reply is a single value/line, and so
# can round-trip through the single-line `_get`/`_set` PVs. Deliberately
# excludes IS (multiline) and the pure commands (MA/MR/SL/HM/S) that don't
# make sense to "read" this way.
_READABLE_SCALAR_PARAMS = tuple(
    m for m in MCODE_PARAMETERS if m not in {"IS", "MA", "MR", "SL", "HM", "S", "HI"}
)

# The MCode variables actually used as *configuration* (`<mnemonic>=<value>`)
# in the original stage-setup cheat-sheet (see schneider_mcode_presets'
# STAGE_PRESETS) -- RC/HC excluded here since they already get dedicated
# `longout` PVs (`_RC`/`_HC`) above, not the generic `_get`/`_set` pair.
# Exposed as real read+write Adjustables (`AdjustableGetSet`).
_CONFIG_PARAM_NAMES = {
    "MS": "microstep_resolution",
    "EL": "encoder_lines",
    "SF": "stall_factor",
    "EE": "encoder_enable",
    "PM": "position_maintenance_enable",
    "DB": "deadband_counts",
    "ER": "error_code",
}

# Diagnostic/state variables: readable through the same interface (confirmed
# live), but not sensible to expose as blind setters (identity/read-only by
# nature, or -- like `P`, redefining position -- a different kind of action
# than "configuration"). Exposed as read-only Detectors (`DetectorGet`).
_READONLY_PARAM_NAMES = {
    "VR": "firmware_version",
    "PN": "part_number",
    "SN": "serial_number",
    "P": "mcode_position",
    "C2": "encoder_counts",
    "MV": "is_moving",
    "V": "mcode_velocity",
    "EF": "error_flag",
}

# `status_collection` selection name for "every setting this module is
# responsible for" -- shared with `eco.devices_general.motors.MotorRecord`
# (which also tags a few of its own top-level fields, e.g. `description`,
# under the same name) so a `eco.elements.memory.SelectionCatalog` for this
# name captures/applies both in one go. See that class's docstring and
# `eco.devices_general.schneider_mcode_presets` for the catalog this feeds.
SETTINGS_SELECTION = "schneider_motor_settings"


class SchneiderMotorSettings(Assembly):
    """Schneider/IMS MForce & MDrive drive settings for one motor channel,
    read/written through the IOC's own `_get`/`_set` MCode passthrough PVs.

    See the module docstring for why this doesn't open a direct serial
    connection, and for the still-unsolved multiline-readback limitation.
    """

    def __init__(
        self,
        pv_motor: str,
        channel: int | str | None = None,
        host: str | None = None,
        console_port: int | None = None,
        name: str = "schneider",
    ):
        """
        Parameters
        ----------
        pv_motor:
            The motor record PV, e.g. ``"SARES20-MF1:MOT_7"``.
        channel:
            MForce channel number. If omitted, parsed from *pv_motor*'s
            ``MOT_<n>`` suffix (matches the convention already used by
            ``MotorRecord(is_psi_mforce=True)``).
        host, console_port:
            Explicit IOC console address, to skip the `eco.epics_utils.iocinfo`
            network lookup that `host_info()`/`console_command()` would
            otherwise do on first use -- much faster when constructing many
            channels at namespace-init time and you already know the
            controller's console (e.g. from a prior `ioc_finder()` search).
            Purely informational (see module docstring); has no effect on
            how settings are read/written.
        """
        super().__init__(name=name)
        controller_base, _, tail = pv_motor.partition(":")
        if channel is None:
            channel = int(tail.split("_")[1])
        self.pv_controller = controller_base
        self.channel = channel
        self._pv_channel = f"{controller_base}:{channel}"
        # `_mcode_get`/`_mcode_set` are one shared PV pair for every
        # mnemonic on this channel -- concurrent get_parameter()/
        # set_parameter() calls (e.g. get_status()'s ThreadPoolExecutor
        # fetching several config Adjustables at once) would otherwise race
        # on them: one thread's write can be overwritten by another's
        # before the first has read its reply. Found live via
        # get_status(selections=["schneider_motor_settings"]) returning
        # empty strings for every generic-passthrough parameter. Serialized
        # here, matching schneider_mcode.SchneiderBus's own lock around its
        # (physically) shared serial port.
        self._mcode_lock = threading.Lock()
        self._host = host
        self._console_port = console_port

        self._append(
            AdjustablePv,
            self._pv_channel + "_RC",
            name="run_current",
            is_setting=True,
            setting_groups=SETTINGS_SELECTION,
        )
        self._append(
            AdjustablePv,
            self._pv_channel + "_HC",
            name="hold_current",
            is_setting=True,
            setting_groups=SETTINGS_SELECTION,
        )
        self._append(
            AdjustablePv,
            self._pv_channel + "_set",
            name="_mcode_set",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self._pv_channel + "_get",
            name="_mcode_get",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            pv_motor + ".MRES",
            name="motor_resolution",
            is_setting=True,
            is_display=False,
            setting_groups=SETTINGS_SELECTION,
        )
        self._append(
            AdjustablePv,
            pv_motor + ".ERES",
            name="encoder_resolution",
            is_setting=True,
            is_display=False,
            setting_groups=SETTINGS_SELECTION,
        )

        # Real read+write Adjustables for every MCode config parameter from
        # the original stage cheat-sheet (RC/HC already handled above via
        # their own dedicated PVs). Each shares the single `_mcode_get`/
        # `_mcode_set` PV pair -- get_parameter()/set_parameter() do the
        # actual bidirectional-`_get`/`_set` protocol (see their docstrings
        # and the module docstring's "EPICS interface, confirmed from
        # source" section for what's really going on underneath).
        for mnemonic, attr_name in _CONFIG_PARAM_NAMES.items():
            self._append(
                AdjustableGetSet,
                (lambda mn=mnemonic: self.get_parameter(mn)),
                (lambda value, mn=mnemonic: self.set_parameter(mn, value)),
                name=attr_name,
                is_setting=True,
                is_display=True,
                setting_groups=SETTINGS_SELECTION,
            )

        # Read-only diagnostics/state, same interface, no setter (see
        # _READONLY_PARAM_NAMES above for why each one isn't a "setting").
        for mnemonic, attr_name in _READONLY_PARAM_NAMES.items():
            self._append(
                DetectorGet,
                (lambda mn=mnemonic: self.get_parameter(mn)),
                name=attr_name,
                is_setting=False,
                is_display=False,
            )

    # -- generic scalar MCode parameter access -----------------------------------
    def get_parameter(self, mnemonic: str, settle: float = 0.2) -> str:
        """Read one scalar MCode variable through `_get`.

        Protocol confirmed live (2026-08-18, `SARES20-MF1:MOT_14`):
        `_get` is bidirectional, not a `_set`-then-`_get` pair as its name
        might suggest -- writing the *bare* mnemonic to `_get` (no `PR `
        prefix) triggers the drive query, and the record is updated with
        the reply after the drive replies, which is not instantaneous
        (empirically ~50-200ms; back-to-back get-after-put without any
        wait returns the not-yet-updated field, observed as the mnemonic
        echoed back rather than a value). `settle` is a blocking
        `time.sleep` after the write, before reading -- crude but matches
        how the existing `MForceSettings`/`MforceChannel` one-shot `_set`
        writes are already used elsewhere in this codebase (fire-and-hope,
        no completion callback available on this PV).

        Verified working live for `VR`/`PN`/`SN`/`MS`/`EF`/`ER`/`RC`/`IS`.
        Only meaningful for parameters whose reply is a single line/value --
        see `eco.devices_general.schneider_mcode_presets.MCODE_PARAMETERS`
        for what each mnemonic means. `IS` returned exactly one line live
        (`'IS = 1, 2, 1'`, matching wire 1's expected default) -- it is
        *not confirmed* whether that's the complete answer or wire 2's line
        got silently dropped by the single-line `_get` record; see
        `describe_input_assignment`.
        """
        with self._mcode_lock:
            self._mcode_get(mnemonic)
            time.sleep(settle)
            return self._mcode_get()

    def set_parameter(self, mnemonic: str, value) -> None:
        """Write one MCode variable (`<mnemonic>=<value>`) through `_set`.

        One-shot, no readback/verification -- same pattern already used by
        `eco.devices_general.motors.MForceSettings.set_limit_switch_config`.
        Lock-protected against interleaving with a concurrent
        `get_parameter()` call on the same channel; see `__init__`.
        """
        with self._mcode_lock:
            self._mcode_set(f"{mnemonic}={value}")

    def read_all_parameters(self) -> dict[str, str]:
        """Snapshot every single-line-readable parameter (see
        `_READABLE_SCALAR_PARAMS`). One CA round-trip per parameter --
        intended for occasional diagnostics, not polling.
        """
        out: dict[str, str] = {}
        for mnemonic in _READABLE_SCALAR_PARAMS:
            try:
                out[mnemonic] = self.get_parameter(mnemonic)
            except Exception as e:
                out[mnemonic] = f"<error: {e}>"
        return out

    # -- deadband / "is close enough" ---------------------------------------------
    def deadband_user_units(self) -> float:
        """Closed-loop position deadband (`DB`, encoder counts) converted to
        the motor record's user units via `.ERES` (falls back to `.MRES` if
        `ERES` reads as 0 -- e.g. an open-loop channel with no encoder
        scaling configured).

        Only meaningful for a channel actually running closed-loop with an
        encoder (`EE=1`/`PM=1`, see `STAGE_PRESETS` for real examples) --
        `DB` is otherwise either unset or not being acted on by the drive.
        """
        db_counts = float(self.get_parameter("DB"))
        eres = self.encoder_resolution()
        scale = eres if eres else self.motor_resolution()
        return abs(db_counts * scale)

    def is_close(self, target: float, current: float, factor: float = 1.0) -> bool:
        """`True` if `current` is within `factor * deadband_user_units()` of
        `target`.

        `factor > 1` for a looser check (e.g. "good enough to proceed"),
        `< 1` for stricter. Uses the *drive's own* closed-loop tolerance
        rather than an arbitrarily-chosen eco-side accuracy, so it tracks
        whatever `DB` the drive is actually configured/tuned with.
        """
        return abs(current - target) <= factor * self.deadband_user_units()

    # -- host / console (informational only, no direct connection) ---------------
    def host_info(self, timeout: float = 10.0):
        """Resolve the owning IOC's host/console via
        `eco.epics_utils.iocinfo.find_ioc` (network REST lookup, a couple
        seconds -- pass `host=`/`console_port=` at construction to skip
        this). Informational only -- for pointing a human at the right
        console (see `eco.widgets.ioc_finder_widget`/`_qt`), not for
        opening a connection from here.

        Uses fuzzy (substring) search -- e.g. `pv_controller`
        `"SARES20-MF1"` is a PV prefix, not the actual IOC name
        (`"SARES20-CSSU-MF1"`, confirmed live 2026-08-18), so an exact-match
        lookup would find nothing; `find_ioc`'s default fuzzy matching finds
        it via the PV records it owns instead. Among any matches, prefers
        one whose device list actually starts with `pv_controller`.
        """
        from ..epics_utils.iocinfo import find_ioc

        matches = find_ioc(self.pv_controller, timeout=timeout)
        if not matches:
            return None
        for m in matches:
            if any(d.startswith(self.pv_controller) for d in m.devices):
                return m
        return matches[0]

    def console_command(self) -> str:
        """The manual `telnet <host> <port>` command for this channel's IOC
        console. Uses `host`/`console_port` from construction if given
        (fast path, no network lookup), else resolves them via
        `host_info()`.
        """
        host, port = self._host, self._console_port
        if not host:
            m = self.host_info()
            if m and m.console_host:
                host, port = m.console_host, m.console_port
        if not host:
            return "<console host unknown -- see host_info()>"
        return f"telnet {host} {port}"

    # -- exploratory: hardware-triggered motion -- NOT wired into anything -------
    def describe_input_assignment(self) -> str:
        """Read the current `IS` (limit-switch/input) config via `_get`.

        **Confirmed incomplete by design, not a guess.** Live-tested
        2026-08-18 on `SARES20-MF1:MOT_14` (configured with `IS=1,2,0` /
        `IS=2,3,0` per `STAGE_PRESETS["qioptic_fusion_zoom"]`):
        `get_parameter("IS")` returned exactly one line, `'IS = 1, 2, 1'`
        -- wire 1 only, wire 2's line never appeared (and its polarity read
        back as `1` where the preset says `0`, possibly just config drift
        since that preset was recorded). Cross-checked against the actual
        IOC source (`/ioc/SARES20-CSSU-MF1/cfg/schneider.cfg`, this host has
        `/ioc` NFS-mounted): the `get` StreamDevice protocol is `out "1pr
        %s"; in "%#s"` -- one command, one captured reply, into a
        40-character `stringin`. There is no protocol path in this IOC's
        database for a multi-line reply to survive, so wire 2 is not
        reachable through this interface at all, not just through this
        method -- see the module docstring's "EPICS interface, confirmed
        from source" section.
        """
        return self.get_parameter("IS")

    def sketch_hw_triggered_move(self, *args, **kwargs):
        """**Exploratory sketch only -- not implemented, not called from
        anywhere in this codebase, safe to ignore.**

        Idea for future work: MForce/MDrive drives assign a function to
        each physical I/O wire via `IS=<wire>,<action>,<polarity>` (see
        `MCODE_PARAMETERS['IS']`). The cheat-sheet this module's parameter
        database was built from
        (`eco.devices_general.schneider_mcode_presets`) only documents
        `<action>` values 0/2/3 (disabled/low-limit-stop/high-limit-stop)
        -- it does *not* document an action code for "use this input to
        trigger a pre-armed move", a feature some IMS/Schneider firmware
        revisions support, but the exact mnemonic is unverified here (no
        manual, no live console access was used to confirm it).

        Before implementing this for real:

        1. Identify a channel with a genuinely *unused* I/O wire -- cross-
           check `STAGE_PRESETS[...]['mcode']['IS']` for every motor
           sharing the same controller; a wire already claimed by another
           channel's limit switch is not free to repurpose.
        2. Find the correct trigger-arm mnemonic in the real firmware
           manual, or by reading `PR IS`/`PR <candidate>` on a bench unit.
        3. Confirm on a bench/non-live setup first -- the same live-
           hardware caution that applies to every write in this module
           applies doubly to reassigning I/O function on a channel that
           may currently be doing something else (e.g. a limit switch).

        Raises `NotImplementedError` on purpose, so calling this by mistake
        fails loudly instead of silently doing nothing (or, worse, sending
        a wrong/guessed command to a live drive).
        """
        raise NotImplementedError(
            "sketch only -- see docstring; not verified against real firmware"
        )
