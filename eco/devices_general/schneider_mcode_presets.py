"""Reference database of Schneider/IMS MCode parameters and known Bernina stage presets.

This is a *data-only* module: it does not talk to EPICS or a serial port. It is
distilled from the accumulated ``caput`` configuration cheat-sheet used to set
up Bernina motion stages behind SARES20-MF1/MF2 MForce controllers, and is meant
as a lookup table -- e.g. to look up what a known stage was configured with, or
as seed data for a future tool that applies/verifies these settings through
either the EPICS ``_set``/``_RC``/``MOT_N.*`` PVs or a direct MCode connection
(see :mod:`eco.devices_general.schneider_mcode`).

Two pieces:

``MCODE_PARAMETERS``
    Mnemonic -> human description, for the raw MCode variable/command space
    (usable with ``SchneiderMotor.get``/``.set`` or the EPICS ``_set``/``_get``
    passthrough PVs). Mnemonics only ever seen assigned via ``<mnemonic>=<value>``
    in the source cheat-sheet, with no first-hand MCode manual to cross-check
    against, are marked ``(meaning inferred from usage; verify against the
    IMS/Schneider MCode reference for the installed firmware)``.

``STAGE_PRESETS``
    One entry per physical stage/axis that has been configured at Bernina.
    Each value has:

    * ``description`` -- free-text label from the source document.
    * ``controller_hint`` -- example PV prefix/channel used when this preset
      was recorded (informational only; re-assign per actual install).
    * ``mcode`` -- dict of MCode variable -> value to write on the drive
      (keys documented in ``MCODE_PARAMETERS``).
    * ``motor_record`` -- dict of EPICS motor-record field -> value
      (``DESC``, ``EGU``, ``MRES``, ``ERES``, ``VELO``, ``DIR``, ...).
    * ``notes`` -- caveats from the source document that don't reduce to a
      single value (wiring quirks, homing direction, measurement procedure).
"""

from __future__ import annotations

# --- MCode parameter reference -------------------------------------------------

_INFERRED = (
    " (meaning inferred from usage in the source cheat-sheet; verify against "
    "the IMS/Schneider MCode reference for the installed firmware)"
)

MCODE_PARAMETERS: dict[str, str] = {
    # -- identification / persistence --
    "VR": "Firmware/version string (read-only).",
    "PN": "Part number (read-only).",
    "SN": "Serial number (read-only).",
    "S": "Save current parameters to non-volatile flash memory (command, no value).",
    # -- motion state / commands --
    "P": "Commanded position counter, in (micro)steps. Writable to redefine current position without moving.",
    "C1": "Raw microstep counter, used together with C2 to empirically calibrate encoder lines (EL); see the Zaber linear-encoder preset notes.",
    "C2": "Encoder counter (requires an encoder-equipped drive).",
    "MV": "Moving flag; nonzero while the drive is executing motion (read-only).",
    "V": "Current running velocity, steps/s (read-only).",
    "MA": "Move absolute to position (command, e.g. 'MA 10000').",
    "MR": "Move relative by distance (command, e.g. 'MR 500').",
    "SL": "Slew continuously at velocity (command, e.g. 'SL 2000').",
    "HM": "Execute a homing routine with the given mode number (command).",
    "HI": "Home input / homing mode selector; observed set to 3 then 1 for opposite homing directions, followed by 'P=0' to zero the position once the home mark is found."
    + _INFERRED,
    # -- motion parameters --
    "VI": "Initial/start velocity, steps/s.",
    "VM": "Maximum/slew velocity, steps/s.",
    "A": "Acceleration, steps/s^2.",
    "D": "Deceleration, steps/s^2.",
    "MS": "Microstep resolution, microsteps per full step.",
    "RC": "Run current, percent of drive maximum (MForce: 100% = 1.5 A). Also exposed directly as the EPICS '<channel>_RC' PV.",
    "HC": "Hold current, percent of drive maximum. Also exposed as the EPICS '<channel>_HC' PV.",
    # -- closed-loop / encoder --
    "EE": "Encoder enable; 1 = use the encoder for closed-loop position correction, 0 = open loop.",
    "EL": "Encoder lines per revolution (or per the configured travel unit), used to relate motor microsteps to encoder counts in closed loop. Best determined empirically (see Zaber linear-encoder preset notes) rather than from the nominal encoder datasheet.",
    "SF": "Stall factor; sensitivity threshold for stall/servo error detection in closed loop."
    + _INFERRED,
    "PM": "Position maintenance enable; 1 = actively hold/correct position against drift after a move completes using the encoder."
    + _INFERRED,
    "DB": "Dead band; allowed position error (encoder counts) before a stall/correction is triggered in closed loop."
    + _INFERRED,
    "ER": "Error/fault register. Written with 0 to clear a latched error during setup; also read to report the last error code.",
    "EF": "Error flag; nonzero if the drive has a latched error (read-only).",
    # -- I/O configuration --
    "IS": (
        "Input setup for a physical limit-switch wire: 'IS=<wire>,<action>,<polarity>'. "
        "wire: 1=low-limit input, 2=high-limit input. "
        "action: 0=no action (disabled), 2=stop on low limit, 3=stop on high limit. "
        "polarity: 0=normally open (active high), 1=normally closed (active low). "
        "To reassign which wire triggers which limit you must first disable both "
        "('IS=1,0,0' / 'IS=2,0,0') before assigning the new action, since the "
        "drive rejects an action that is already claimed by the other wire."
    ),
}

# Convenience decode tables for the IS= command, matching MCODE_PARAMETERS["IS"].
IS_WIRE = {1: "low limit switch", 2: "high limit switch"}
IS_ACTION = {0: "no action / disabled", 2: "stop on low limit", 3: "stop on high limit"}
IS_POLARITY = {0: "normally open (active high)", 1: "normally closed (active low)"}


# --- Known stage presets --------------------------------------------------------

STAGE_PRESETS: dict[str, dict] = {
    # -- USD chamber XYZ, open loop --
    "usd_chamber_x_open_loop": {
        "description": "USD chamber X (open loop)",
        "controller_hint": "SARES20-MF2 channel 1",
        "mcode": {"RC": 40},
        "motor_record": {"DESC": "USD X", "MRES": 1.953125e-05, "EGU": "mm", "DIR": 1},
        "notes": [],
    },
    "usd_chamber_y_open_loop": {
        "description": "USD chamber Y (open loop)",
        "controller_hint": "SARES20-MF2 channel 2",
        "mcode": {"RC": 40},
        "motor_record": {"DESC": "USD Y", "MRES": 9.765625e-06, "EGU": "mm", "DIR": 1},
        "notes": [],
    },
    "usd_chamber_z_open_loop": {
        "description": "USD chamber Z (open loop)",
        "controller_hint": "SARES20-MF2 channel 3",
        "mcode": {"RC": 40},
        "motor_record": {"DESC": "USD Z", "MRES": 1.953125e-05, "EGU": "mm", "DIR": 1},
        "notes": [],
    },
    # -- USD chamber XYZ, closed loop --
    "usd_chamber_x_closed_loop": {
        "description": "USD chamber X (closed loop)",
        "controller_hint": "SARES20-MF2 channel 1",
        "mcode": {"RC": 40, "EL": 2500, "SF": 500, "EE": 1, "PM": 1, "DB": 20, "ER": 0},
        "motor_record": {
            "DESC": "USD X",
            "MRES": 0.0001,
            "ERES": 0.0001,
            "RDBD": 0.005,
            "EGU": "mm",
            "DIR": 1,
        },
        "notes": ["Save to flash with the 'S' command after configuring."],
    },
    "usd_chamber_y_closed_loop": {
        "description": "USD chamber Y (closed loop)",
        "controller_hint": "SARES20-MF2 channel 2",
        "mcode": {"RC": 40, "EL": 1250, "SF": 500, "EE": 1, "PM": 1, "DB": 20, "ER": 0},
        "motor_record": {
            "DESC": "USD Y",
            "MRES": 0.0001,
            "ERES": 0.0001,
            "RDBD": 0.005,
            "EGU": "mm",
            "DIR": 1,
        },
        "notes": ["Save to flash with the 'S' command after configuring."],
    },
    "usd_chamber_z_closed_loop": {
        "description": "USD chamber Z (closed loop)",
        "controller_hint": "SARES20-MF2 channel 3",
        "mcode": {"RC": 40, "EL": 2500, "SF": 500, "EE": 1, "PM": 1, "DB": 20, "ER": 0},
        "motor_record": {
            "DESC": "USD Z",
            "MRES": 0.0001,
            "ERES": 0.0001,
            "RDBD": 0.005,
            "EGU": "mm",
            "DIR": 1,
        },
        "notes": ["Save to flash with the 'S' command after configuring."],
    },
    # -- Steinmeyer stage --
    "steinmeyer_x_closed_loop": {
        "description": "LIC Steinmeyer X (closed loop)",
        "controller_hint": "SARES20-MF2 channel 5",
        "mcode": {"EL": 12500, "SF": 500, "EE": 1, "PM": 1, "DB": 20, "ER": 0},
        "motor_record": {
            "DESC": "LIC Steinmeyer X",
            "MRES": 0.0001,
            "ERES": 0.0001,
            "EGU": "mm",
            "DIR": 0,
            "VELO": 5,
        },
        "notes": [
            "Homing (forward): mcode HI=3, wait until the home mark is found, "
            "then mcode P=0 to zero the position, then set motor-record SYNC=1 "
            "to resync RBV/VAL.",
            "Homing (reverse direction): mcode HI=1, then P=0.",
        ],
    },
    # -- Zoom stages (Qioptic / Navitar) --
    "qioptic_fusion_zoom": {
        "description": "Qioptic Optem Fusion zoom",
        "controller_hint": "SARES20-MF1 channel 14",
        "mcode": {"RC": 8, "IS": [(1, 2, 0), (2, 3, 0)]},
        "motor_record": {
            "DESC": "Qioptic Zoom",
            "EGU": "%",
            "MRES": 0.00002886,
            "ERES": 0.00002886,
            "VELO": 10,
        },
        "notes": [],
    },
    "navitar_zoom_xeye": {
        "description": "Navitar zoom (Timetool / prof_kb / xray eye)",
        "controller_hint": "SARES20-MF1 channel 14",
        "mcode": {"RC": 8, "IS": [(1, 2, 0), (2, 3, 0)]},
        "motor_record": {
            "DESC": "xeye Zoom",
            "EGU": "%",
            "MRES": 0.00002886,
            "ERES": 0.00002886,
            "VELO": 10,
        },
        "notes": [],
    },
    "navitar_zoom_7000": {
        "description": "Navitar Zoom 7000 (THz chamber, 45 deg incidence)",
        "controller_hint": "SARES20-MF1 channel 15",
        "mcode": {"RC": 8, "IS": [(1, 2, 0), (2, 3, 0)]},
        "motor_record": {
            "DESC": "THz chamber zoom 45 inc",
            "EGU": "%",
            "MRES": 0.00002886,
            "ERES": 0.00002886,
            "VELO": 10,
            "DIR": 1,
        },
        "notes": [],
    },
    "profkb_zoom_ch8": {
        "description": "Prof_KB zoom (variant seen on channel 8)",
        "controller_hint": "SARES20-MF1 channel 8",
        "mcode": {"RC": 8, "IS": [(1, 2, 0), (2, 3, 0)]},
        "motor_record": {
            "DESC": "Prof_KB_ZOOM",
            "EGU": "%",
            "MRES": 0.00002886,
            "ERES": 0.00002886,
            "VELO": 10,
        },
        "notes": ["Near-duplicate of profkb_zoom_ch7 in the source document; channel differs."],
    },
    "profkb_zoom_ch7": {
        "description": "Prof_kb zoom (variant seen on channel 7)",
        "controller_hint": "SARES20-MF1 channel 7",
        "mcode": {"RC": 8, "IS": [(1, 2, 0), (2, 3, 0)]},
        "motor_record": {
            "DESC": "Prof_kb zoom",
            "EGU": "%",
            "MRES": 0.00002886,
            "ERES": 0.00002886,
            "VELO": 10,
        },
        "notes": ["Near-duplicate of profkb_zoom_ch8 in the source document; channel differs."],
    },
    "xeye_zoom_ch6": {
        "description": "xeye zoom (variant seen on channel 6, inverted IS polarity)",
        "controller_hint": "SARES20-MF1 channel 6",
        "mcode": {"RC": 8, "IS": [(1, 1, 0), (2, 1, 0)]},
        "motor_record": {
            "DESC": "xeye zoom",
            "EGU": "%",
            "MRES": 0.00002886,
            "ERES": 0.00002886,
            "VELO": 10,
        },
        "notes": [
            "IS action codes here are both '1', which is not a documented action "
            "in MCODE_PARAMETERS['IS'] (0/2/3 are); transcribed as-is from the "
            "source document, likely a typo for '2'/'3' -- verify before applying.",
        ],
    },
    "ana_pitch": {
        "description": "ANA_pitch (only DESC recorded in source document)",
        "controller_hint": "SARES20-MF1 channel 3",
        "mcode": {},
        "motor_record": {"DESC": "ANA_pitch"},
        "notes": ["Source document has no other parameters for this channel."],
    },
    # -- Micos stages --
    "micos_vt80": {
        "description": "Micos VT-80 (no encoder)",
        "controller_hint": "SARES20-MF1 channel 4",
        "mcode": {"RC": 30, "IS": [(1, 0, 0), (2, 0, 0), (1, 3, 1), (2, 2, 1)]},
        "motor_record": {
            "DESC": "GC x position",
            "EGU": "mm",
            "MRES": 1.953125e-05,
            "ERES": 1.953125e-05,
            "VELO": 1,
            "DIR": 1,
        },
        "notes": [
            "Max run current 80% (1.2 A); 30% used in practice.",
            "IS sequence clears both switches then reassigns wire 1 to high limit "
            "and wire 2 to low limit (swapped vs. the IS_WIRE default), both "
            "normally-closed.",
        ],
    },
    "micos_kern_vert": {
        "description": "Kern Vert Micos (linear, vertical)",
        "controller_hint": "SARES20-MF2 channel 11",
        "mcode": {"RC": 30, "IS": [(1, 2, 1), (2, 3, 1)]},
        "motor_record": {
            "DESC": "Kern Vert Micos",
            "EGU": "mm",
            "MRES": 2.421875e-06,
            "ERES": 2.421875e-06,
            "VELO": 1,
        },
        "notes": [
            "200 steps/rev x 256 microsteps/step = 51200 usteps/rev = 1 mm/rev "
            "-> MRES = 1 mm / 51200 = 1.953125e-05 nominal for this leadscrew "
            "pitch family; this particular axis's recorded MRES differs "
            "(2.421875e-06), i.e. a different pitch/reduction -- taken as-is "
            "from the source document.",
        ],
    },
    "micos_kern_horiz": {
        "description": "Kern Horiz Micos (linear, horizontal, 2 mm/rev)",
        "controller_hint": "SARES20-MF1 channel 5",
        "mcode": {"RC": 30, "IS": [(1, 0, 0), (2, 0, 0)]},
        "motor_record": {
            "DESC": "Kern Horiz Micos",
            "EGU": "mm",
            "MRES": 3.90625e-05,
            "ERES": 3.90625e-05,
            "VELO": 1,
        },
        "notes": ["2 mm/rev -> MRES = 2 mm / 51200 usteps/rev = 3.90625e-05."],
    },
    # -- Zaber stages --
    "zaber_lift_vsr20_t3": {
        "description": "Small Zaber lift stage VSR20-T3 (LZP y)",
        "controller_hint": "SARES20-MF1 channel 13",
        "mcode": {"RC": 30, "IS": [(2, 0, 0), (1, 2, 1)]},
        "motor_record": {
            "DESC": "LZP y",
            "EGU": "mm",
            "DIR": 1,
            "MRES": 2.4765000000000004e-05,
            "ERES": 2.4765000000000004e-05,
            "VELO": 1,
        },
        "notes": [
            "No high-limit switch exists on this stage; never command it too high.",
            "Set a soft limit ~20 mm from the low limit switch instead.",
            "The limit switch only works with the dedicated limit-switch adapter "
            "cable (adapter cables are wired incorrectly otherwise).",
            "By default the positive direction is DOWN on this stage.",
        ],
    },
    "zaber_rotational_nd_wheel": {
        "description": "Zaber rotational stage, ND wheel",
        "controller_hint": "SARES20-MF1 channel 16",
        "mcode": {"RC": 100, "IS": [(1, 0, 1), (2, 0, 1)]},
        "motor_record": {
            "DESC": "ND wheel Zaber rot",
            "EGU": "deg",
            "VELO": 10,
            "MRES": 0.00140500,
            "ERES": 1.00000000,
        },
        "notes": [
            "Vendor-specified microstep size is 0.005625 deg; recorded MRES "
            "(0.001405) does not match that directly -- source document flags "
            "ERES as 'can be wrong' too.",
        ],
    },
    "zaber_linear_encoder_open_loop": {
        "description": "Zaber LRQ150AL-DE51T3 linear stage w/ encoder, run encoderless (open loop) with backlash compensation",
        "controller_hint": "SARES20-MF1 channel 7",
        "mcode": {"RC": 30, "IS": [(1, 2, 1), (2, 3, 1)], "PM": 0, "EE": 0},
        "motor_record": {
            "DESC": "LRQ150AL-DE51T3",
            "EGU": "mm",
            "MRES": 2.48046875e-05,
            "ERES": 2.48046875e-05,
            "VELO": 1,
            "BVEL": 0.2,
            "BACC": 0.2,
            "BDST": 0.01,
            "FRAC": 1,
        },
        "notes": [
            "51200 usteps/rev = 1.27 mm/rev -> MRES = 1.27 mm / 51200 = 2.48046875e-05.",
            "Alternate seen for the first IS line: 'IS=1,2,0' (normally-open) "
            "instead of 'IS=1,2,1'.",
        ],
    },
    "zaber_linear_encoder_closed_loop": {
        "description": "Zaber LRQ150AL-DE51T3 linear stage w/ encoder, closed loop",
        "controller_hint": "SARES20-MF1 channel 7",
        "mcode": {"RC": 30, "EL": 6368, "SF": 10000, "PM": 1, "EE": 1},
        "motor_record": {"EGU": "mm", "MRES": 0.00005, "ERES": 0.00005, "VELO": 1},
        "notes": [
            "Encoder resolution is 50 nm. Nominal calc: 200 steps/rev x 256 "
            "usteps/step = 51200 usteps/rev = 1.27 mm/rev; 1.27 mm / 50 nm = "
            "25400 encoder counts/rev = 25400/4 = 6350 encoder lines/rev "
            "(1 line = 4 counts) -> nominal EL=6350.",
            "The nominal EL was found to be slightly off in practice; the "
            "precise value (EL=6368 here) should be measured per unit: go open "
            "loop (EE=0, PM=0), zero C1/C2 (optional), move exactly N "
            "revolutions, read C1 (microsteps) and C2 (encoder counts) again, "
            "then EL = (delta C2) / 4.",
        ],
    },
    "zaber_rotation_with_rot_encoder": {
        "description": "Zaber rotation stage with rotational encoder",
        "controller_hint": "SARES20-MF16(?) channel 16",
        "mcode": {"RC": 30, "IS": [(2, 0, 0), (1, 0, 0), (1, 2, 1), (2, 0, 0)]},
        "motor_record": {"EGU": "mm", "MRES": 2.48046875e-05, "ERES": 2.48046875e-05, "VELO": 1},
        "notes": [
            "Transcribed as-is from the source document, which appears to be a "
            "copy/paste of the zaber_linear_encoder_open_loop block: the "
            "controller prefix ('SARES20-MF16') doesn't match the MF1/MF2 "
            "naming used everywhere else, EGU is 'mm' for a stage described as "
            "rotational, and wire 2's IS action is listed twice with "
            "conflicting third-vs-fourth-line values. Verify against the "
            "physical install before use.",
        ],
    },
    # -- JJ slits --
    "jj_slit_large_kf40": {
        "description": "JJ slit blade, large (KF40), single blade",
        "controller_hint": "SARES20-MF1 channel 3",
        "mcode": {"RC": 20, "IS": [(2, 0, 0), (1, 0, 0), (1, 2, 1), (2, 3, 1)]},
        "motor_record": {
            "DESC": "JJ slit blade",
            "EGU": "mm",
            "MRES": 1.953125e-06,
            "ERES": 1.953125e-06,
            "VELO": 0.5,
            "ACCL": 0.2,
        },
        "notes": ["1 um per full step, assuming 512x microstepping."],
    },
    "jj_slit_small_kf25": {
        "description": "JJ slit gap/pos, small (KF25), two blades moving together",
        "controller_hint": "SARES20-MF1 channel 4",
        "mcode": {"RC": 20, "IS": [(2, 0, 0), (1, 0, 0)]},
        "motor_record": {
            "DESC": "Small JJ slit gap / pos",
            "EGU": "mm",
            "MRES": 1.953125e-06,
            "ERES": 1.953125e-06,
            "VELO": 0.5,
            "ACCL": 0.2,
        },
        "notes": ["Two blades always move together; 1 um per full step, assuming 512x microstepping."],
    },
    # -- Kohzu --
    "kohzu_ra07a_rotation": {
        "description": "Kohzu RA07A-W rotation stage",
        "controller_hint": "SARES20-MF1 channel 4",
        "mcode": {
            "RC": 80,
            "HC": 0,
            "MS": 128,
            "IS": [(2, 0, 0), (1, 0, 0), (1, 2, 1), (2, 3, 1)],
        },
        "motor_record": {
            "DESC": "Kohzu RA07A",
            "EGU": "deg",
            "MRES": 0.00007813,
            "ERES": 0.00007813,
            "VELO": 0.5,
            "ACCL": 0.2,
        },
        "notes": ["~4 deg/rev, 200 steps/rev, 128x microstepping -> MRES = 4 / 200 / 128."],
    },
}


def format_mcode_setup(preset_name: str) -> list[str]:
    """Render the ``mcode`` block of a preset as MCode command lines.

    Convenience for eyeballing a preset or feeding it to
    :meth:`eco.devices_general.schneider_mcode.SchneiderMotor.command`. Does
    not touch EPICS or a serial port itself.
    """
    preset = STAGE_PRESETS[preset_name]
    lines = []
    for key, value in preset["mcode"].items():
        if key == "IS":
            for wire, action, polarity in value:
                lines.append(f"IS={wire},{action},{polarity}")
        else:
            lines.append(f"{key}={value}")
    return lines


# --- Bridge to eco.elements.memory.SelectionCatalog -----------------------------
#
# STAGE_PRESETS above is a static, hand-maintained Python reference table.
# This converts it into the reloadable, JSON-backed
# `SelectionCatalog("schneider_motor_settings")` format that
# `MotorRecord(schneider=True)`/`SchneiderMotorSettings` actually expose
# (via their shared `setting_groups=SchneiderMotorSettings.SETTINGS_SELECTION`
# tagging -- see that module) -- i.e. presets you can `.apply()` to a real
# motor, not just read.
#
# Only fields that map onto something actually tagged into that selection
# are carried over (see `_MCODE_KEY_TO_ATTR`/`_MOTOR_RECORD_KEY_TO_ATTR`
# below); `IS` and the optional-kwarg-gated motor-record fields (`RDBD`,
# `BVEL`, `BACC`, `BDST`, `FRAC` -- only present at all if `MotorRecord` was
# built with `resolution_pars=True`/`backlash_definition=True`) are
# deliberately dropped rather than guessed into an entry that would then
# fail (or silently mismatch) on `apply()`. See `seed_schneider_motor_settings_catalog`'s
# docstring for exactly what each converted preset omits.

# STAGE_PRESETS['...']['mcode'] key -> SchneiderMotorSettings attribute name
# (relative to the MotorRecord, i.e. "schneider_settings.<attr>").
_MCODE_KEY_TO_ATTR = {
    "RC": "run_current",
    "HC": "hold_current",
    "MS": "microstep_resolution",
    "EL": "encoder_lines",
    "SF": "stall_factor",
    "EE": "encoder_enable",
    "PM": "position_maintenance_enable",
    "DB": "deadband_counts",
    "ER": "error_code",
    # "IS" intentionally omitted -- see MCODE_PARAMETERS['IS'] and
    # SchneiderMotorSettings.describe_input_assignment for why it can't
    # round-trip through this interface at all, set or get.
}

# STAGE_PRESETS['...']['motor_record'] key -> MotorRecord top-level
# attribute name (unconditionally present, tagged in motors.py).
_MOTOR_RECORD_KEY_TO_TOP_ATTR = {
    "DESC": "description",
    "EGU": "unit",
    "DIR": "direction",
    "VELO": "speed",
    "ACCL": "acceleration_time",
}

# STAGE_PRESETS['...']['motor_record'] key -> SchneiderMotorSettings
# attribute name (MRES/ERES are duplicated there specifically so they're
# present whenever `schneider=True`, without also requiring
# `resolution_pars=True`; see that module).
_MOTOR_RECORD_KEY_TO_SCHNEIDER_ATTR = {
    "MRES": "motor_resolution",
    "ERES": "encoder_resolution",
}


def preset_to_catalog_values(preset_name: str) -> dict:
    """Convert one `STAGE_PRESETS` entry into a `{relative_dotted_name:
    value}` dict in `SelectionCatalog("schneider_motor_settings")`'s
    format -- i.e. what `preset_to_catalog_values(...)` would need to look
    like to `SelectionCatalog.register()`/`.apply()` onto a real
    `MotorRecord(schneider=True)` instance.

    Drops anything that doesn't map onto a currently-tagged attribute (see
    module-level comment above) rather than guessing.
    """
    preset = STAGE_PRESETS[preset_name]
    values = {}
    for mcode_key, value in preset.get("mcode", {}).items():
        attr = _MCODE_KEY_TO_ATTR.get(mcode_key)
        if attr is not None:
            values[f"schneider_settings.{attr}"] = value
    for mr_key, value in preset.get("motor_record", {}).items():
        top_attr = _MOTOR_RECORD_KEY_TO_TOP_ATTR.get(mr_key)
        if top_attr is not None:
            values[top_attr] = value
            continue
        sch_attr = _MOTOR_RECORD_KEY_TO_SCHNEIDER_ATTR.get(mr_key)
        if sch_attr is not None:
            values[f"schneider_settings.{sch_attr}"] = value
    return values


def seed_schneider_motor_settings_catalog(catalog, overwrite=False, presets=None):
    """Register every entry of `STAGE_PRESETS` (or just `presets`, an
    iterable of names, if given) into an
    `eco.elements.memory.SelectionCatalog("schneider_motor_settings")`
    instance you provide, converted via `preset_to_catalog_values`.

    Does not create or touch a catalog directory itself -- pass an already-
    constructed `catalog` (e.g. `SelectionCatalog("schneider_motor_settings",
    catalog_dir=...)`) so the caller controls exactly where this writes.
    Entries that already exist are skipped unless `overwrite=True`. Returns
    the list of preset names actually (re)registered.
    """
    names = presets if presets is not None else STAGE_PRESETS.keys()
    written = []
    for name in names:
        if not overwrite and name in catalog.names():
            continue
        values = preset_to_catalog_values(name)
        if not values:
            continue
        catalog.register(
            name,
            values,
            message=STAGE_PRESETS[name].get("description"),
            overwrite=overwrite,
        )
        written.append(name)
    return written
