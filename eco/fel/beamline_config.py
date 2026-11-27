"""Aramis/Athos beamline and mode switching, ported from PSI's ``BeamLineConfig``.

``/sf/controls/bin/BeamLineConfig`` (Thierry Zamofing, beta, see JIRA
SFELPHOTON-1753) runs ``/sf/controls/applications/synoptipy/synTopo/
BeamLineConfig.py``, also used by the synTopo synoptic panel. It does not use
an IOC-side switching record. Instead it

1. knows which optics devices take part in each endstation
   (:data:`BEAMLINE_DEVICES`, its ``lutBl``),
2. reads setpoint *records* that the OBLL IOC module (``gitea.psi.ch:
   epics_ioc_modules/OBLL``) serves for each device as ``<device>:<AXIS>_<tags>``,
   e.g. ``SAROP21-OOMV092:RX_PINK`` or ``SAROP11-OOMH064:TX_ESB_ESC``, plus
   one deadband record per axis, ``<device>:<AXIS>_DB``,
3. picks the setpoints whose tags match the target endstation/mode, and
4. moves every motor axis (``W_RX``, ``W_Y``, ... for mirrors, ``MOTOR_Y``
   for apertures) that is outside its deadband (all of them with
   ``force=True``), waits, and re-issues the whole device if a motor stalled.

The setpoint *values* always come from EPICS (they can be changed live in
the IOC); only the record *names* come from ``SetPoints.yaml`` in that module
(:data:`SETPOINTS_FILE`, readable on the consoles), with a copy embedded here
(:data:`EMBEDDED_SETPOINT_RECORDS`) for hosts where it is not mounted.

In eco this is ``fel.beamline_config_tmp`` (`BeamlineConfig`): per branch an
``endstation`` and a ``mode`` enum adjustable, plus ``plan()``/``switch()``.

Differences from ``BeamLineConfig.py``, all deliberate:

- **Setpoint selection by tag, not by yaml order.** A setpoint applies if its
  endstation tags (if any) contain the target endstation *and* its mode tags
  (if any) contain the target mode. The original accepted a match on
  *either*, letting the last one in the file win, so for ESD in ``mono``
  mode ``SATOP11-OFMH091`` was sent to ``RX_ESD_PINK``/``TY_ESD_PINK``.
  ``OUT`` setpoints apply only to axes nothing else matched, as before. For
  every Aramis case the two rules give the same result
  (``tests/test_fel_beamline_config.py``).
- **No move on incomplete data**: an unreadable setpoint, deadband or
  readback raises before anything moves (the original crashed half-way
  through on a ``None`` readback).
- Moves go through eco ``MotorRecord`` (access/claim gates, stall
  detection) instead of raw ``caput``s.
- When the Aramis endstation changes, the front-end photon shutter
  ``SARFE10-OPSH059`` is closed first, as the old IOC switch
  ``SAROP-ARAMIS:BEAMLINE_SP`` does (``close_shutter=False`` to skip).
  It is not reopened.

**Not covered, same as in the original:** photon energy (its ``energy``
argument is unused) and the Bernina DCM. The old IOC ``SAROP21-ARAMIS:MODE_SP``
also set ``SAROP21-ODCM098:MODE_SP``; this does not, so after switching
Bernina to ``fix``, set the mono yourself. ``chechDevices`` (homing check,
needs Zamofing's private ``OOM.generate``) and ``moveDefault`` are not ported.
"""

import logging
import re
import time
from dataclasses import dataclass
from enum import IntEnum

from ..aliases import Alias
from ..devices_general.utilities import Changer
from ..elements.adjustable import default_representation, spec_convenience
from ..elements.assembly import Assembly
from ..epics_utils.detector import DetectorPvEnum

_log = logging.getLogger(__name__)

SETPOINTS_FILE = "/ioc/modules/OBLL/SetPoints.yaml"

#: the optics devices moved for each endstation (``lutBl`` in BeamLineConfig.py)
BEAMLINE_DEVICES = {
    "A": ("SAROP11-OOMH064", "SAROP11-OOMH076", "SAROP31-OOMH067",
          "SAROP11-OOMV108", "SAROP11-OOMV109"),
    "B": ("SAROP11-OOMH064", "SAROP31-OOMH067",
          "SAROP21-OOMV092", "SAROP21-OOMV096"),
    "C": ("SAROP11-OOMH064", "SAROP31-OOMH067",
          "SAROP31-OOMH084", "SAROP31-ODMV152"),
    "D": ("SATOP11-OOMV076", "SATOP11-OOMV077", "SATOP11-OAPU085",
          "SATOP11-OAPU089", "SATOP11-OFMH091"),
    "E": ("SATOP11-OOMV076", "SATOP11-OOMV077", "SATOP11-OAPU085",
          "SATOP11-OAPU089", "SATOP11-OFMH091", "SATOP31-OFMH094"),
    "F": ("SATOP11-OOMV076", "SATOP11-OOMV077", "SATOP11-OAPU085",
          "SATOP11-OAPU089", "SATOP11-OFMH091", "SATOP31-OFMH094"),
}

#: endstation names accepted besides ``"esa"``..``"esf"``/``"A"``..``"F"``.
#: Aramis 1-3 = Alvra, Bernina, Cristallina (OBLL BeamlineSelection_SAR.db);
#: Athos D/E/F = Diavolezza, Maloja, Furka, from the old setpoint aliases
#: (``RX_D_M_SET``, ``TY_M_M_SET``, ``TY_FURKA_PINK_SET``).
ENDSTATION_NAMES = {"alvra": "A", "bernina": "B", "cristallina": "C",
                    "diavolezza": "D", "maloja": "E", "furka": "F"}

#: endstations of each branch, in enum order (value = position + 1, 0 = undefined)
BRANCHES = {"aramis": ("alvra", "bernina", "cristallina"),
            "athos": ("diavolezza", "maloja", "furka")}

#: modes offered per branch (Aramis setpoints have no MONO, Athos no FIX/VAR)
BRANCH_MODES = {"aramis": ("PINK", "FIX", "VAR"), "athos": ("PINK", "MONO")}

MODES = ("PINK", "FIX", "VAR", "MONO")

#: first column of SetPoints.yaml (OBLL, file dated 2025-09-30), used when
#: :data:`SETPOINTS_FILE` cannot be read
EMBEDDED_SETPOINT_RECORDS = {
    "SAROP11-OOMH064": ("RY_DB", "TX_DB", "TX_ESA", "RY_ESA", "TX_ESB_ESC"),
    "SAROP11-OOMH076": ("RY_DB", "TX_DB", "TX_ESA", "RY_ESA"),
    "SAROP11-OOMV108": ("RX_DB", "TY_DB", "RX_FIX", "TY_PINK", "TY_FIX"),
    "SAROP11-OOMV109": ("RX_DB", "TY_DB", "RX_PINK", "TY_PINK", "RX_FIX",
                        "TY_FIX", "TY_VAR"),
    "SAROP21-OOMV092": ("RX_DB", "TY_DB", "RX_PINK", "TY_PINK", "RX_FIX",
                        "TY_FIX"),
    "SAROP21-OOMV096": ("RX_DB", "TY_DB", "RX_PINK", "TY_PINK"),
    "SAROP31-ODMV152": (),
    "SAROP31-OOMH067": ("TX_DB", "RY_DB", "RZ_DB", "TX_ESA", "TX_ESB",
                        "TX_ESC", "RY_ESA", "RY_ESB", "RY_ESC", "RZ_ESA",
                        "RZ_ESB", "RZ_ESC"),
    "SAROP31-OOMH084": ("RY_DB", "TX_DB", "TX_ESC", "RY_ESC"),
    "SATOP11-OAPU085": ("TY_DIRECT_SET_", "TY_ESF_PINK", "TY_DB"),
    "SATOP11-OAPU089": ("TY_ESF", "TY_ESE_MONO", "TY_ESE_PINK", "TY_DB"),
    "SATOP11-OFMH091": ("RX_ESD_MONO", "RX_ESD_PINK", "RX_OUT", "RX_DB",
                        "RY_ESD", "RY_OUT", "RY_DB", "RZ_ESD", "RZ_DB",
                        "TY_ESD_MONO", "TY_ESD_PINK", "TY_OUT", "TY_DB",
                        "TX_ESD", "TX_OUT", "TX_DB"),
    "SATOP11-OOMH073": ("RX_IN", "RX_DB", "RY_IN", "RY_DB", "RZ_IN", "RZ_DB",
                        "TX_IN", "TX_DB", "TY_IN", "TY_DB"),
    "SATOP11-OOMV076": ("RX_ESF_PINK", "RX_OUT", "RX_DB", "RZ_ESF_PINK",
                        "RZ_OUT", "RZ_DB", "TY_ESF_PINK", "TY_OUT", "TY_DB"),
    "SATOP11-OOMV077": ("RX_ESF_PINK", "RX_OUT", "RX_DB", "RZ_ESF_PINK",
                        "RZ_OUT", "RZ_DB", "TY_ESF_PINK", "TY_OUT", "TY_DB"),
    "SATOP11-SWITCH": ("running_",),
    "SATOP31-OFMH094": ("RX_ESF", "RX_ESE", "RX_DB", "RY_ESF", "RY_ESE",
                        "RY_DB", "RZ_ESF", "RZ_DB", "TX_ESF", "TX_ESE",
                        "TX_DB", "TY_ESF", "TY_ESE_MONO", "TY_ESE_PINK",
                        "TY_DB"),
}


def load_setpoint_records(path=SETPOINTS_FILE):
    """``{device: (record suffix, ...)}`` from OBLL's SetPoints.yaml, or the
    embedded copy if the file can't be read."""
    try:
        import yaml

        with open(path) as fh:
            raw = yaml.safe_load(fh)
        return {dev: tuple(str(e[0]).strip() for e in (entries or ()))
                for dev, entries in raw.items()}
    except Exception as exc:
        _log.debug("using embedded setpoint names (%s: %s)", path, exc)
        return dict(EMBEDDED_SETPOINT_RECORDS)


def normalize_endstation(endstation):
    """``"bernina"``/``"esb"``/``"b"``/``"B"`` -> ``"B"``."""
    s = str(endstation).strip().lower()
    s = ENDSTATION_NAMES.get(s, s)
    if s.lower().startswith("es") and len(s) == 3:
        s = s[2]
    s = s.upper()
    if s not in BEAMLINE_DEVICES:
        raise ValueError(
            f"unknown endstation {endstation!r}; use one of "
            f"{sorted(ENDSTATION_NAMES)} or esa..esf")
    return s


def normalize_mode(mode):
    """``None`` stays ``None``; ``"pink"``/``"p"``/``"Fix Offset"`` -> ``"PINK"``/``"FIX"``."""
    if mode is None:
        return None
    words = str(mode).strip().upper().split()
    for m in MODES:
        if words and m.startswith(words[0]):
            return m
    raise ValueError(f"unknown mode {mode!r}; use one of {[m.lower() for m in MODES]}")


def motor_field(device, axis):
    """Setpoint axis -> motor record field: ``TX`` -> ``W_X``, ``RY`` -> ``W_RY``
    on mirrors, ``TY`` -> ``MOTOR_Y`` on apertures (OAPU)."""
    ax = axis[1:] if axis.startswith("T") else axis
    return ("MOTOR_" if "OAPU" in device else "W_") + ax


@dataclass
class AxisPlan:
    """What a switch does with one motor axis of one device."""

    device: str
    axis: str  # setpoint naming, e.g. "TX"
    field: str  # motor record field, e.g. "W_X"
    setpoint_record: str = None  # e.g. "TX_ESB_ESC"; None = no setpoint applies
    deadband_record: str = None
    setpoint: float = None
    deadband: float = None
    readback: float = None
    move: bool = False
    note: str = ""

    @property
    def motor_pv(self):
        return f"{self.device}:{self.field}"

    @property
    def target(self):
        """Where this axis should end up (its readback if no setpoint applies)."""
        return self.setpoint if self.setpoint is not None else self.readback


def select_setpoints(device, records, endstation, mode=None):
    """Choose the setpoint record for each axis of ``device``.

    Pure function, no EPICS access. Returns ``{axis: (setpoint_record or None,
    deadband_record or None)}`` for every axis that has any record.
    """
    es_tag = "ES" + endstation
    chosen, out, deadband = {}, {}, {}
    for rec in records:
        axis, *tags = rec.split("_")
        tags = [t for t in tags if t]
        if not re.fullmatch(r"[TR][XYZ]", axis) or not tags:
            continue  # "running_" etc.
        if tags == ["DB"]:
            deadband[axis] = rec
            continue
        chosen.setdefault(axis, None)
        if tags == ["OUT"]:
            out.setdefault(axis, rec)
            continue
        es_tags = {t for t in tags if t.startswith("ES")}
        mode_tags = {t for t in tags if t in MODES}
        if not es_tags and not mode_tags:
            continue  # "IN", "DIRECT_SET": not part of switching
        if es_tags and es_tag not in es_tags:
            continue
        if mode_tags and mode not in mode_tags:
            continue
        if chosen[axis] is not None:
            _log.warning("%s: both %s and %s match %s/%s, using %s",
                         device, chosen[axis], rec, es_tag, mode, rec)
        chosen[axis] = rec
    for axis, rec in out.items():
        if chosen.get(axis) is None:
            chosen[axis] = rec
    axes = list(dict.fromkeys(list(chosen) + list(deadband)))
    return {ax: (chosen.get(ax), deadband.get(ax)) for ax in axes}


def plan_axes(endstation, mode=None, records=None):
    """``[AxisPlan, ...]`` (values not read yet) for switching to ``endstation``."""
    es = normalize_endstation(endstation)
    mode = normalize_mode(mode)
    records = load_setpoint_records() if records is None else records
    plans = []
    for dev in BEAMLINE_DEVICES[es]:
        for axis, (sp, db) in select_setpoints(dev, records.get(dev, ()), es, mode).items():
            plans.append(AxisPlan(dev, axis, motor_field(dev, axis), sp, db))
    return plans


def decide_moves(plans, force=False):
    """Fill ``move``/``note`` from the values read into ``plans``."""
    for p in plans:
        if p.setpoint_record is None:
            p.note = "no setpoint for this endstation/mode, stays"
        elif p.deadband_record is None:
            p.note = "setpoint has no deadband record, not moved (as BeamLineConfig)"
        elif force:
            p.move, p.note = True, "forced"
        elif abs(p.readback - p.setpoint) > p.deadband:
            p.move, p.note = True, f"off by {p.readback - p.setpoint:+.4g}"
        else:
            p.note = "within deadband"
    return plans


def format_plan(plans, title=""):
    rows = [("device", "motor", "setpoint record", "readback", "target", "deadband", "")]
    fmt = lambda v: "-" if v is None else f"{v:.4g}"
    for p in plans:
        rows.append((p.device, p.field, p.setpoint_record or "-", fmt(p.readback),
                     fmt(p.target), fmt(p.deadband), ("MOVE  " if p.move else "      ") + p.note))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    lines = [title] if title else []
    for r in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1])
    return "\n".join(lines)




def mode_tags(record):
    """Mode tags of a setpoint record name: ``"TY_ESD_MONO"`` -> ``{"MONO"}``."""
    return {t for t in record.split("_")[1:] if t in MODES} if record else set()


def available_modes(endstation, records=None):
    """Modes that have at least one setpoint at ``endstation``, in MODES order."""
    es = normalize_endstation(endstation)
    records = load_setpoint_records() if records is None else records
    found = set()
    for dev in BEAMLINE_DEVICES[es]:
        for rec in records.get(dev, ()):
            tags = set(rec.split("_")[1:])
            es_tags = {t for t in tags if t.startswith("ES")}
            if not es_tags or "ES" + es in es_tags:
                found |= tags & set(MODES)
    return tuple(m for m in MODES if m in found)


def in_position(plans, mode_only=False):
    """True if every axis with a setpoint and a deadband (only the mode-tagged
    ones with ``mode_only``) is within its deadband, and there is at least one."""
    checked = [p for p in plans
               if p.setpoint_record and p.deadband_record
               and (not mode_only or mode_tags(p.setpoint_record))]
    return bool(checked) and all(
        abs(p.readback - p.setpoint) <= p.deadband for p in checked)


def branch_of(endstation):
    name = endstation_name(endstation)
    return next(b for b, names in BRANCHES.items() if name in names)


def endstation_name(endstation):
    """``"esb"``/``"B"``/``"bernina"`` -> ``"bernina"``."""
    es = normalize_endstation(getattr(endstation, "name", endstation))
    return next(n for n, l in ENDSTATION_NAMES.items() if l == es)


# ---------------------------------------------------------------------------
# EPICS side


class BeamlineSwitchError(RuntimeError):
    pass


def _caget(pvnames, what):
    from epics import caget_many

    pvnames = list(dict.fromkeys(pvnames))
    values = caget_many(pvnames, timeout=2.0) if pvnames else []
    missing = [n for n, v in zip(pvnames, values) if v is None]
    if missing:
        raise BeamlineSwitchError(f"could not read {what}: {', '.join(missing)}")
    return dict(zip(pvnames, values))


def _fill(plans, values):
    for p in plans:
        if p.setpoint_record:
            p.setpoint = float(values[f"{p.device}:{p.setpoint_record}"])
        if p.deadband_record:
            p.deadband = float(values[f"{p.device}:{p.deadband_record}"])
        p.readback = float(values[p.motor_pv + ".RBV"])
    return plans


def _pvs_of(plans):
    return [f"{p.device}:{r}" for p in plans
            for r in (p.setpoint_record, p.deadband_record) if r] + \
        [p.motor_pv + ".RBV" for p in plans]


@default_representation
@spec_convenience
class BranchSelector:
    """Enum adjustable for the endstation or the mode of one branch.

    Reading compares motor readbacks with the OBLL setpoints (``undefined``
    if no choice is in position). Setting runs `BranchConfig.switch`:
    ``endstation`` switches with ``mode=None`` (mode-dependent axes stay),
    ``mode`` switches the current endstation to that mode."""

    def __init__(self, branch, kind, name=None):
        self.name = name
        self.alias = Alias(name)
        self._branch = branch
        self._kind = kind
        choices = (BRANCHES[branch.branch] if kind == "endstation"
                   else [m.lower() for m in BRANCH_MODES[branch.branch]])
        self.enum_strs = list(choices)
        self.value_enum = IntEnum(
            f"{branch.branch.capitalize()}{kind.capitalize()}",
            {"undefined": 0, **{c: n + 1 for n, c in enumerate(choices)}})

    def validate(self, value):
        if isinstance(value, str):
            v = (endstation_name(value) if self._kind == "endstation"
                 else normalize_mode(value).lower())
            if v not in self.enum_strs:
                raise ValueError(f"{value!r} is not one of {self.enum_strs}")
            return self.value_enum[v]
        v = self.value_enum(value)
        if v.name == "undefined":
            raise ValueError("cannot switch to 'undefined'")
        return v

    def get_current_value(self):
        state = self._branch._state()
        return self.value_enum[state[self._kind] or "undefined"]

    def set_target_value(self, value, hold=False):
        value = self.validate(value)
        return Changer(
            target=value,
            parent=self,
            changer=lambda v: self._branch.switch(**{self._kind: v.name}),
            hold=hold,
            stopper=self._branch.stop,
        )

    def stop(self):
        self._branch.stop()


class BranchConfig(Assembly):
    """Endstation and mode of one branch (``aramis`` or ``athos``), see
    `BeamlineConfig`."""

    STATE_MAX_AGE = 0.5  # s; endstation + mode in one status read share it

    def __init__(self, branch, name=None, shutter_pv=None):
        super().__init__(name=name)
        self.branch = branch
        self._shutter_pv = shutter_pv
        self._motors = {}
        self._changers = []
        self._state_cache = (0.0, None)
        self._append(BranchSelector, self, "endstation", name="endstation",
                     is_display=True, is_setting=False)
        self._append(BranchSelector, self, "mode", name="mode",
                     is_display=True, is_setting=False)
        if branch == "aramis":
            # the OBLL IOC's own readbacks, for comparison
            for pv, nm in (("SAROP-ARAMIS:BEAMLINE", "endstation_ioc"),
                           ("SAROP11-ARAMIS:MODE", "alvra_mode_ioc"),
                           ("SAROP21-ARAMIS:MODE", "bernina_mode_ioc")):
                self._append(DetectorPvEnum, pv, name=nm, is_display=True)

    # -- reading

    def _all_plans(self, records):
        out = []
        for name in BRANCHES[self.branch]:
            for mode in (None,) + available_modes(name, records):
                out.append(((name, mode), plan_axes(name, mode, records)))
        return out

    def _state(self):
        """``{"endstation": name or None, "mode": lower-case mode or None}``,
        from one read of every setpoint and readback of the branch."""
        t, cached = self._state_cache
        if cached is not None and time.time() - t < self.STATE_MAX_AGE:
            return cached
        records = load_setpoint_records()
        plans = self._all_plans(records)
        values = _caget([pv for _, ps in plans for pv in _pvs_of(ps)],
                        f"{self.branch} setpoints/readbacks")
        es = mode = None
        for (name, m), ps in plans:
            if m is None and es is None and in_position(_fill(ps, values)):
                es = name
        for (name, m), ps in plans:
            if es == name and m is not None and in_position(_fill(ps, values), mode_only=True):
                mode = m.lower()
                break
        state = {"endstation": es, "mode": mode}
        self._state_cache = (time.time(), state)
        return state

    def available_modes(self, endstation=None):
        """Modes with setpoints at ``endstation`` (default: the current one)."""
        es = endstation or self.endstation.get_current_value().name
        return [m.lower() for m in available_modes(es)]

    def _resolve(self, endstation, mode):
        if endstation is None:
            endstation = self._state()["endstation"]
            if endstation is None:
                raise BeamlineSwitchError(
                    f"{self.branch} endstation is undefined, give one explicitly")
        endstation = endstation_name(endstation)
        if endstation not in BRANCHES[self.branch]:
            raise ValueError(f"{endstation} is not on {self.branch} {BRANCHES[self.branch]}")
        if mode is not None:
            mode = normalize_mode(mode)
            if mode not in available_modes(endstation):
                raise ValueError(f"{endstation} has no {mode.lower()} setpoints; "
                                 f"available: {self.available_modes(endstation)}")
        return endstation, mode

    def plan(self, endstation=None, mode=None, force=False, show=True):
        """Read setpoints and readbacks and show what `switch` would move."""
        endstation, mode = self._resolve(endstation, mode)
        plans = plan_axes(endstation, mode)
        decide_moves(_fill(plans, _caget(_pvs_of(plans), "setpoints/readbacks")), force)
        if show:
            print(format_plan(plans, f"switch to {endstation}"
                                     f"{'' if mode is None else ' ' + mode.lower()}:"))
        return plans

    # -- moving

    def _motor(self, pvname):
        if pvname not in self._motors:
            from ..devices_general.motors import MotorRecord

            self._motors[pvname] = MotorRecord(
                pvname, name=pvname.replace("-", "_").replace(":", "_").lower())
        return self._motors[pvname]

    def _close_shutter_if_switching(self, endstation):
        if not self._shutter_pv or self._state()["endstation"] == endstation:
            return
        from ..xoptics.shutters import PhotonShutter

        print(f"closing {self._shutter_pv.split(':')[0]} before switching to {endstation}")
        PhotonShutter(self._shutter_pv, name="pshut_fe").request.set_target_value(0).wait()

    def _move(self, plans, retries=2):
        by_device = {}
        for p in plans:
            by_device.setdefault(p.device, []).append(p)
        todo = {dev: [p for p in ps if p.move] for dev, ps in by_device.items()}
        todo = {dev: ps for dev, ps in todo.items() if ps}
        for attempt in range(retries + 1):
            changers = [(dev, p, self._motor(p.motor_pv).set_target_value(p.target))
                        for dev, ps in todo.items() for p in ps]
            self._changers = [c for _, _, c in changers]
            failed = {}
            for dev, p, ch in changers:
                try:
                    ch.wait()
                except KeyboardInterrupt:
                    self.stop()
                    raise
                except Exception as exc:
                    failed.setdefault(dev, []).append(f"{p.field}: {exc}")
            self._changers = []
            if not failed:
                return
            if attempt == retries:
                raise BeamlineSwitchError(
                    "motion failed after retries: " + "; ".join(
                        f"{dev} ({', '.join(m)})" for dev, m in failed.items()))
            # like BeamLineConfig: re-issue every axis of a stalled device,
            # the ones that did not need to move back to their start position
            _log.warning("motion stalled on %s, re-issuing", ", ".join(failed))
            print(f"motion stalled on {', '.join(failed)}, retrying ({attempt + 1}/{retries})")
            todo = {dev: [p for p in by_device[dev]
                          if p.move or p.setpoint_record is None or p.deadband_record]
                    for dev in failed}
            time.sleep(2)

    def switch(self, endstation=None, mode=None, force=False, close_shutter=True, retries=2):
        """Move the optics to ``endstation`` (default: current) and ``mode``
        (``None``: mode-dependent axes stay). Prints the plan first, blocks."""
        endstation, mode = self._resolve(endstation, mode)
        plans = self.plan(endstation, mode, force)
        if not any(p.move for p in plans):
            print("nothing to move")
            return plans
        if close_shutter:
            self._close_shutter_if_switching(endstation)
        try:
            self._move(plans, retries=retries)
        finally:
            self._state_cache = (0.0, None)
        print("done")
        return plans

    def stop(self):
        for c in list(self._changers):
            try:
                c.stop()
            except Exception:
                pass


class BeamlineConfig(Assembly):
    """Beamline and mode switching of SwissFEL's photon branches, as
    ``/sf/controls/bin/BeamLineConfig`` does it (beta port, hence the
    ``fel.beamline_config_tmp`` name; algorithm and differences in the
    `eco.fel.beamline_config` module docstring).

    Layout::

        fel.beamline_config_tmp
            .aramis.endstation   enum  alvra | bernina | cristallina
            .aramis.mode         enum  pink | fix | var
            .aramis.endstation_ioc, .alvra_mode_ioc, .bernina_mode_ioc
                                 the OBLL IOC's own readbacks
            .athos.endstation    enum  diavolezza | maloja | furka
            .athos.mode          enum  pink | mono

    ``endstation`` and ``mode`` are ordinary eco enum adjustables (dropdowns
    in the widget). Their *readback* is computed from the motors: the
    endstation whose setpoint axes are all within their deadbands, and the
    mode whose mode-dependent axes are. ``undefined`` means none is.
    *Setting* one moves the optics:

    - ``aramis.endstation.mv("bernina")`` switches with mode-dependent axes
      left alone (closing the front-end shutter ``SARFE10-OPSH059`` first if
      the endstation changes; it is not reopened);
    - ``aramis.mode.mv("pink")`` switches the current endstation to pink;
    - ``switch("bernina", "pink")`` does both in one go;
    - ``plan("bernina", "pink")`` only prints which axes would move.

    ``athos`` is not in the status display (its ``SATOP11-OOMV077`` motors
    did not answer from the Bernina consoles); use it directly.

    Not every mode exists everywhere (Bernina: pink/fix, Cristallina none):
    ``aramis.available_modes("bernina")``. Energy and the Bernina DCM are
    *not* changed: after switching Bernina to ``fix``, set the mono yourself.
    Setpoint values are read live from the OBLL IOC (``<device>:<AXIS>_<tag>``
    records); edit them there, not here. Docs: ``eco/fel/beamline_config.py``
    module docstring; upstream JIRA SFELPHOTON-1753.
    """

    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(BranchConfig, "aramis", shutter_pv="SARFE10-OPSH059:REQUEST",
                     name="aramis", is_display="recursive", is_setting=False)
        # not in the status display: SATOP11-OOMV077's motors did not answer
        # from the Bernina consoles (2026-11-27), so every read would wait
        # for the CA timeout and raise
        self._append(BranchConfig, "athos", name="athos",
                     is_display=False, is_setting=False)

    def _branch(self, endstation):
        return getattr(self, branch_of(endstation))

    def plan(self, endstation, mode=None, force=False, show=True):
        """Show what `switch` would move (reads only)."""
        return self._branch(endstation).plan(endstation, mode, force=force, show=show)

    def switch(self, endstation, mode=None, force=False, close_shutter=True):
        """Switch ``endstation`` (any of both branches) to ``mode``; blocks."""
        return self._branch(endstation).switch(
            endstation, mode, force=force, close_shutter=close_shutter)

    def stop(self):
        self.aramis.stop()
        self.athos.stop()
