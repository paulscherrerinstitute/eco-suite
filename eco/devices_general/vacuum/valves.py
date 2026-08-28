"""Pneumatic vacuum valves and prepump (fore-vacuum) controls.

Modelled from the SwissFEL VCS caqtdm panel. Two device families live here:

* :class:`Valve` -- pneumatic gate/pump valves (device tags ``...-VVPG...`` and
  ``...-VVPP...``), from ``S_VCS__valve_h1.ui`` / ``S_VCS__valve_v2.ui`` (the two
  differ only in drawing orientation) and the status subpanel
  ``S_VCS__Valve_ErrorMessage.ui``.
* :class:`PrePump` -- fore-vacuum / backing pump start-stop (device tags
  ``...-VPFO...``), from ``S_VCS__VPR1.ui``.

Minimal use::

    from eco.devices_general.vacuum import Valve, ValveState
    v = Valve("SARES21-VVPG140-240", name="valve_test")
    v.open()      # request open
    v.close()     # request close
    v.get_current_value()   # -> ValveState.OPEN / ValveState.CLOSED / ValveState.MOVING
"""

from enum import Enum

from eco import Assembly
from eco.elements.detector import DetectorVirtual
from eco.epics_utils.adjustable import AdjustablePv, AdjustablePvEnum
from eco.epics_utils.detector import DetectorPvData, DetectorPvEnum, DetectorPvString


class ValveState(str, Enum):
    """Enumeration for valve states (enum-compatible with strings)."""
    OPEN = "open"
    CLOSED = "closed"
    MOVING = "moving"
    UNDEFINED = "undefined"
    INCONSISTENT = "inconsistent"
    UNKNOWN = "unknown"


#: The PLC's own :REQUEST command PV uses imperative verbs (live-verified
#: enum_strs: ``("close", "open")``), which is NOT the same spelling as
#: ValveState's resulting-state values (``"closed"``, not ``"close"``) --
#: mapped explicitly rather than assumed interchangeable, since the mismatch
#: is silent (a plain KeyError from deep inside AdjustablePvEnum.validate,
#: not an obviously-valve-related error) and previously caused
#: ``Valve.close()``/``set_target_value(ValveState.CLOSED)`` to always raise.
_COMMAND_TO_STATE = {"open": ValveState.OPEN, "close": ValveState.CLOSED}
_STATE_TO_COMMAND = {state: command for command, state in _COMMAND_TO_STATE.items()}


class Valve(Assembly):
    """A pneumatic vacuum valve.

    Exactly two status children, since that's all there is to show: the
    write channel :attr:`request` (an enum, ``open``/``close``) and the true
    state :attr:`readback` (collapsed from the two raw PLC limit switches,
    which stay internal -- there's nothing to gain from showing both a raw
    open switch and a raw closed switch alongside the state they already
    combine into). :meth:`get_current_value`/:meth:`set_target_value`/
    :attr:`enum_strs` on the Valve itself are plain delegates to those two,
    so the valve is directly usable as an Adjustable/Detector without
    reaching into either child; :meth:`open`/:meth:`close` are further
    convenience wrappers around :meth:`set_target_value`. :attr:`is_open`
    and :attr:`error_message` are still there for programmatic use (a
    beamline's transmission logic, PLC fault text) but out of the default
    status view -- they're both derived from/duplicate of `readback`.
    """

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # open/close command sent to the PLC (enum: "open"/"close")
        self._append(
            AdjustablePvEnum, f"{pvbase}:REQUEST", name="request", is_setting=True
        )
        # PLC limit-switch readbacks (enum ON/OFF); both ON/OFF => moving/fault.
        # Internal only -- get_current_value() below collapses them into the
        # single `readback` child, so showing these two raw switches as well
        # would just be the same information twice.
        #
        # NOTE `is_display=False` is what actually keeps a child out of the
        # shell table / `repr(valve)`: `__repr__` -> `get_display_str()` reads
        # the "display" status_collection selection, which `_append` fills
        # from `is_display` (`is_status` feeds `get_status()`/the status
        # server instead). Both are set here -- these are internal plumbing
        # for either purpose.
        self._append(DetectorPvEnum, f"{pvbase}:PLC_OPEN", name="_open_readback",
                     is_display=False, is_status=False)
        self._append(DetectorPvEnum, f"{pvbase}:PLC_CLOSE", name="_closed_readback",
                     is_display=False, is_status=False)
        # the valve's true state, collapsed from the two switches above --
        # literally `self.get_current_value` re-exposed as a child Detector
        self._append(DetectorVirtual, [], self.get_current_value, name="readback")
        # human-readable PLC status/fault text for this valve (diagnostic,
        # not part of the default display)
        self._append(
            DetectorPvString, f"{pvbase}:PLC_ER_MESSAGE", name="error_message",
            is_display=False, is_status=False,
        )
        # boolean "beam can pass" view of the state, so a beamline model's
        # transmission logic (Beamline.diagram / _read_open_state) can read
        # this valve the same way it reads any other blocking component.
        # Pure duplicate of `readback` for display purposes, so hidden there.
        self._append(
            DetectorVirtual, [], lambda: self.get_current_value() == ValveState.OPEN,
            name="is_open", is_setting=False, is_display=False, is_status=False,
        )

    def get_current_value(self, *args, **kwargs):
        """Collapse the open/closed PLC readbacks into a single state string."""
        try:
            is_open = self._open_readback.get_current_value().name == "ON"
            is_closed = self._closed_readback.get_current_value().name == "ON"
        except Exception:
            return ValveState.UNKNOWN
        if is_open and not is_closed:
            return ValveState.OPEN
        if is_closed and not is_open:
            return ValveState.CLOSED
        if is_open and is_closed:
            return ValveState.INCONSISTENT
        return ValveState.MOVING

    @property
    def enum_strs(self):
        """The settable command choices only (e.g. OPEN/CLOSED) -- NOT
        get_current_value()'s full collapsed-readback state space
        (MOVING/INCONSISTENT/UNDEFINED/UNKNOWN are synthesized read-only
        states, never legal write targets). Sourced from the underlying
        command PV's own enum table (`request`), translated via
        `_COMMAND_TO_STATE` to ValveState's upper-case member names so widget
        pulldowns -- which pre-select by matching get_current_value()'s
        `.name` -- still highlight the right entry instead of falling back to
        `enum.Enum.__members__` (every ValveState member)."""
        out = []
        for s in self.request.enum_strs:
            state = _COMMAND_TO_STATE.get(s)
            out.append(state.name if state is not None else s)
        return tuple(out)

    def set_target_value(self, value):
        """Set the valve target state (ValveState.OPEN/CLOSED, or the
        matching name string, e.g. "OPEN" -- what `enum_strs`/widget
        pulldowns use -- or the raw PV command string, e.g. "open"/"close").
        ValveState -> command goes through `_STATE_TO_COMMAND`, NOT
        `.value` -- CLOSED.value is "closed", but the PLC command is "close"
        (see `_COMMAND_TO_STATE`'s docstring)."""
        if isinstance(value, str) and value in ValveState.__members__:
            value = ValveState[value]
        if isinstance(value, ValveState):
            value = _STATE_TO_COMMAND.get(value, value.value)
        return self.request.set_target_value(value)

    def open(self):
        """Request the valve to open."""
        return self.set_target_value(ValveState.OPEN)

    def close(self):
        """Request the valve to close."""
        return self.set_target_value(ValveState.CLOSED)


class FastValve(Assembly):
    """A fast (protection) valve (``...-VVFV...``), from ``S_VCS__FastValve.ui``.

    Unlike :class:`Valve`, a fast valve is a **status-only** device in the VCS:
    it slams shut automatically on a pressure spike, and its arming/reset lives
    in the PLC / upstream optics panel rather than here. Same convention as
    :class:`Valve`'s readback side (no write channel here, obviously): the two
    raw PLC limit switches stay internal, collapsed into one :attr:`readback`
    status child by :meth:`get_current_value`; :attr:`is_open` is out of the
    default status view too, same reasoning as `Valve.is_open`.
    """

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # PLC limit-switch readbacks (enum ON/OFF) -- internal, see Valve
        # (incl. why hiding needs is_display, not just is_status)
        self._append(DetectorPvEnum, f"{pvbase}:PLC_OPEN", name="_open_readback",
                     is_display=False, is_status=False)
        self._append(DetectorPvEnum, f"{pvbase}:PLC_CLOSE", name="_closed_readback",
                     is_display=False, is_status=False)
        # the valve's true state, collapsed from the two switches above
        self._append(DetectorVirtual, [], self.get_current_value, name="readback")
        # boolean "beam can pass" view (see Valve.is_open)
        self._append(
            DetectorVirtual, [], lambda: self.get_current_value() == ValveState.OPEN,
            name="is_open", is_setting=False, is_display=False, is_status=False,
        )

    def get_current_value(self, *args, **kwargs):
        """Collapse the open/closed PLC readbacks into a single state string."""
        try:
            is_open = self._open_readback.get_current_value().name == "ON"
            is_closed = self._closed_readback.get_current_value().name == "ON"
        except Exception:
            return ValveState.UNKNOWN
        if is_open and not is_closed:
            return ValveState.OPEN
        if is_closed and not is_open:
            return ValveState.CLOSED
        if is_open and is_closed:
            return ValveState.INCONSISTENT
        return ValveState.MOVING


class PrePump(Assembly):
    """Fore-vacuum / backing (pre-)pump start-stop control (``...-VPFO...``).

    :attr:`request` starts/stops the pump; :attr:`ready` is the PLC ready
    interlock readback.
    """

    def __init__(self, pvbase, name=None):
        super().__init__(name=name)
        self.pvbase = pvbase
        # start/stop command sent to the PLC (enum: "start"/"stop")
        self._append(
            AdjustablePvEnum, f"{pvbase}:REQUEST", name="request", is_setting=True
        )
        # PLC "ready to run" interlock readback (enum ON/OFF)
        self._append(DetectorPvEnum, f"{pvbase}:PLC_READY", name="ready")
        # numeric backing setpoint on some controllers; optional because not
        # every VPFO device populates it.
        self._append(
            AdjustablePv, f"{pvbase}:SET", name="set", is_setting=True, optional=True
        )

    def start(self):
        """Request the pre-pump to start."""
        return self.request.set_target_value("start")

    def stop(self):
        """Request the pre-pump to stop."""
        return self.request.set_target_value("stop")

    def get_current_value(self, *args, **kwargs):
        return self.request.get_current_value()
