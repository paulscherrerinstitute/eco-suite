"""Draft `Beamline` model of the Bernina beamline.

Assembled from the components and z-positions found in `eco.bernina.bernina`
(the live `Namespace` definition), `eco.bernina.bernina_beamline`,
`eco.bernina.config` (an older, dict-based layout with "z_und" = meters
downstream of the Aramis undulator for most components) and
`eco.xoptics.kb_bernina.KBMirrorBernina` (mm-precision distances from the
sample point, also used by `xoptics/applet_vis.ipynb`).

This is a **first draft for discussion, not a validated/maintained beamline
model**:

- Positions come from two different eras/conventions that were never fully
  cross-checked against each other or the as-built hardware -- treat every
  z-number here as "roughly right, please verify" (see the
  `eco.xoptics.beamline_assembly` module docstring for the `z_source` [m]
  vs. `z_sample` [mm] distinction).
- Vacuum stations (`vac_switch`, `vac_slit_mono`, `vac_att`, `vac_kb`) use
  *real* valve/gauge/pump PVs recovered from `xoptics/Aramis.ui` (a caQtDM
  synoptic covering the whole Aramis branch, valid for both Alvra and
  Bernina) -- specifically the ``NAME=...,PUMP=...,GAUGE=...,VALVE1=...,
  VALVE2=...`` macro strings that feed its related-display buttons. Gauge/
  pump PV *suffixes* (e.g. whether pressure is `:PRESSURE` or something
  else) were not visible in that file, only the device prefix -- flagged
  per station below; verify against the actual gauge IOC before relying on
  a reading. No dedicated beam-blocking "safety stopper" PV was found
  anywhere in that file (as opposed to the hutch-wide `SafetyShutter`,
  which is real and already wired in) -- `SafetyStopper`/`VacuumSection`'s
  `pvname_stopper` stays available for if/when one is identified.
- One `kind="zone"` entry (`bernina_branch_selected`) demonstrates the
  "beam-path currently active" concept: `Aramis.ui` lights up a beam-path
  segment in yellow via a `visibilityCalc` ANDing a route/mode selector
  with the intervening valve+shutter being open (e.g. `caPolyLine_5`:
  ``"((A=2)&&(B=1))&&(C=1)"`` on `SAROP-ARAMIS:BEAMLINE` / a vacuum valve /
  `SARFE10-OPSH059:PLC_OPEN`). Which raw integer of `SAROP-ARAMIS:BEAMLINE`
  means "Bernina" wasn't independently confirmed here, so this reads the
  same routing state by *name* instead, through the already-existing
  `eco.xoptics.beamline_mode.AramisMode` wrapper -- self-documenting and
  sidesteps that guess.
- Hardware constructors are imported lazily, inside each factory function,
  so importing this module never requires a live EPICS connection or any
  device-specific optional dependency; only *calling* a factory on the
  actual control-system network does.
- Lives under `eco.xoptics` rather than `eco.bernina` on purpose:
  `eco/bernina/__init__.py` does `from .bernina import *`, which runs the
  whole live control-room startup script (needs a live EPICS network plus
  `datahub`) as a side effect of importing *anything* from that package.
  Keeping this module under `eco.xoptics` (empty `__init__.py`) means it can
  be imported and played with (see `demo_layout()`) without any of that.

Usage::

    from eco.xoptics.beamline_bernina import (
        make_bernina_front_end, make_bernina_experiment_hutch,
    )

    front_end = make_bernina_front_end()
    exp_hutch = make_bernina_experiment_hutch(xp=front_end.xp)
    bernina = front_end + exp_hutch          # concatenated into one beamline

    bernina.show_layout(ref="sample")        # or ref="source"
    bernina.plot_layout(ref="sample")
    bernina.plot_beam_sizes(energy_eV=12400) # fits+plots from live prof monitors

See `demo_layout()` at the bottom for a hardware-free sanity check (no
EPICS/control-system access needed) of the position table / layout plot /
beam-size fit machinery, using the same names and positions as the two
factories above but without touching any real device.
"""

from lazy_object_proxy import Proxy

from .beamline_assembly import (
    GaussianBeam,
    ProfileMonitor,
    VacuumSection,
    Beamline,
    energy_to_wavelength_mm,
)

#: nominal Bernina sample position, in "meters downstream of the Aramis
#: undulator" -- see the historical "z_und" comments in `eco.bernina.config`.
Z0_SOURCE_BERNINA_SAMPLE = 142.0

#: nominal working photon energy [eV], only used as the default for the
#: beam-size fits below -- override per-measurement/call as needed.
DEFAULT_ENERGY_EV = 12400.0


def _bernina_branch_active_condition():
    """Build the zero-arg predicate for the `bernina_branch_selected` zone
    (see `make_bernina_front_end`): is the Aramis beam currently routed to
    Bernina, with the front-end vacuum valve and photon shutter open?

    Modelled on `Aramis.ui`'s ``caPolyLine_5`` visibilityCalc
    ``"((A=2)&&(B=1))&&(C=1)"`` on channels ``SAROP-ARAMIS:BEAMLINE`` /
    ``SARFE10-SBST060:PLC_VCS_OPEN1`` / ``SARFE10-OPSH059:PLC_OPEN`` --
    except the routing channel is read by *name* here (through the
    already-existing `eco.xoptics.beamline_mode.AramisMode`) rather than by
    that raw integer, since which value of ``SAROP-ARAMIS:BEAMLINE`` means
    "Bernina" wasn't independently confirmed against real hardware here.
    Note `AramisMode.switch` is backed by ``SAROP-ARAMIS:BEAMLINE_SP`` (the
    setpoint), not the plain ``SAROP-ARAMIS:BEAMLINE`` readback used in
    `Aramis.ui` -- the two should track each other, but that wasn't
    independently confirmed either; it's simply the most convenient
    already-existing named access to this routing state.

    Building `AramisMode` and the two raw PVs is itself deferred to the
    first call of the returned `condition()` (not done here), so that
    merely *registering* this zone (see `make_bernina_front_end`) stays as
    lazy/EPICS-free as every other `add_component(..., lazy=True)` entry.
    """
    state = {}

    def _connect():
        from epics import PV

        from .beamline_mode import AramisMode

        state["mode"] = AramisMode(name="_aramis_mode_for_zone")
        state["valve_open"] = PV("SARFE10-SBST060:PLC_VCS_OPEN1")
        state["shutter_open"] = PV("SARFE10-OPSH059:PLC_OPEN")

    def condition():
        if not state:
            _connect()
        route = state["mode"].switch.get_current_value().name
        return (
            route.lower() == "bernina"
            and bool(state["valve_open"].get())
            and bool(state["shutter_open"].get())
        )

    return condition


def make_bernina_front_end(name="bernina_front_end"):
    """Aramis undulator -> Bernina optics-hutch attenuator/slit/diode, all
    positioned via `z_source` [m] (the historical "z_und" convention). Real,
    currently-configured PVs throughout (see `eco.bernina.bernina`),
    reconstructed here as a standalone `Beamline` rather than pulled
    from the live `Namespace`.
    """
    from ..xoptics.shutters import PhotonShutter, SafetyShutter
    from ..xoptics.attenuator_aramis import AttenuatorAramis
    from ..xoptics.slits import JJSlitUnd, SlitBlades, SlitPosWidth
    from ..xoptics.offsetMirrors_new import OffsetMirrorsBernina
    from ..xoptics.dcm_new import DoubleCrystalMono
    from ..xoptics.pp import Pulsepick
    from ..xdiagnostics.profile_monitors import Pprm
    from ..xdiagnostics.intensity_monitors import SolidTargetDetectorPBPS
    from ..endstations.bernina_vacuum import BerninaVacuum

    bl = Beamline(
        name=name,
        z0_source=Z0_SOURCE_BERNINA_SAMPLE,
        source_name="Aramis undulator",
        energy_eV=DEFAULT_ENERGY_EV,
        description="Undulator to Bernina optics-hutch attenuator/slit (SAROP21-OAPU138).",
    )

    bl.add_component(
        PhotonShutter, "SARFE10-OPSH044:REQUEST", name="pshut_und", lazy=True,
        z_source=44.0, kind="shutter", description="first shutter after the undulators",
    )
    bl.add_component(
        JJSlitUnd, name="slit_und", lazy=True,
        z_source=44.0, kind="slit", description="slit right after the undulator",
    )
    bl.add_component(
        Pprm, "SARFE10-PPRM064", "SARFE10-PPRM064", name="prof_fe", lazy=True, in_target=3,
        z_source=64.0, kind="profile", description="front-end profile monitor",
    )
    bl.add_component(
        # `Proxy(lambda: bl.pshut_und)` rather than `bl.pshut_und` directly:
        # the latter would resolve (build) the lazy `pshut_und` right here,
        # while defining `att_fe`, defeating its own laziness.
        AttenuatorAramis, "SARFE10-OATT053", shutter=Proxy(lambda: bl.pshut_und), set_limits=[],
        name="att_fe", lazy=True, z_source=53.0, kind="attenuator",
        description="front-end attenuator",
    )
    bl.add_component(
        SolidTargetDetectorPBPS, "SARFE10-PBPS053", use_calibration=False,
        pipeline_computation="SARFE10-PBPS053_proc", name="mon_und", lazy=True,
        z_source=53.0, kind="diagnostic",
        description="intensity/position monitor after the undulator",
    )
    bl.add_component(
        PhotonShutter, "SARFE10-OPSH059:REQUEST", name="pshut_fe", lazy=True,
        z_source=59.0, kind="shutter", description="photon shutter, end of front end",
    )
    bl.add_component(
        Pprm, "SAROP11-PPRM066", "SAROP11-PPRM066", name="prof_mirr_alv1", lazy=True, in_target=3,
        z_source=66.0, kind="profile",
        description="shared Aramis switchyard profile monitor, upstream of the Alvra/Bernina split",
    )
    bl.add_component(
        SlitBlades, "SAROP21-OAPU092", name="slit_switch", lazy=True,
        z_source=92.0, kind="slit", description="switchyard slit",
    )
    # Real station from Aramis.ui: "NAME=OAPU092_ADC,P=SAROP21-OAPU092,
    # PUMP=SAROP11-VPIG090-030,GAUGE=SAROP11-VMFR090-030,
    # VALVE1=SAROP11-VVPG087-030,VALVE2=SAROP11-VVPG091-010" (note the
    # PUMP/GAUGE/VALVE PVs sit under the SAROP11 (Alvra-side) IOC even
    # though the slit itself is SAROP21/Bernina-numbered -- this segment is
    # upstream of the Alvra/Bernina split and shared).
    bl.add_component(
        VacuumSection,
        pvname_valve=["SAROP11-VVPG087-030:PLC_OPEN", "SAROP11-VVPG091-010:PLC_OPEN"],
        pvname_gauge="SAROP11-VMFR090-030",
        pvname_pump="SAROP11-VPIG090-030",
        name="vac_switch", lazy=True, z_source=90.0, kind="vacuum",
        description="isolation valves + gauge + pump around the switchyard slit (real PVs)",
    )
    bl.add_component(
        OffsetMirrorsBernina, name="offset", lazy=True,
        z_source=94.0, kind="mirror", description="offset mirror pair (mirr1@92m, mirr2@96m)",
    )
    bl.add_component(
        Pprm, "SAROP21-PPRM094", "SAROP21-PPRM094", name="prof_mirr1", lazy=True, in_target=3,
        z_source=94.0, kind="profile",
    )
    bl.add_component(
        DoubleCrystalMono, pvname="SAROP21-ODCM098", name="mono", lazy=True,
        z_source=98.0, kind="mono", description="Si(111) double-crystal monochromator",
    )
    bl.add_component(
        SlitBlades, "SAROP21-OAPU102", name="slit_mono", lazy=True,
        z_source=102.0, kind="slit",
    )
    # Real station from Aramis.ui: "NAME=OAPU102,P=SAROP21-OAPU102,
    # PUMP=SAROP21-VPIG103-060,GAUGE=SAROP21-VPIG103-060,
    # VALVE1=SAROP21-VVPG098-040,VALVE2=SAROP21-VVPG104-050" -- note PUMP and
    # GAUGE point at the *same* PV in that macro; that looks like a copy/paste
    # artifact in the .ui file rather than a real gauge PV, so `pvname_gauge`
    # is left unset here rather than guessing -- verify on site.
    bl.add_component(
        VacuumSection,
        pvname_valve=["SAROP21-VVPG098-040:PLC_OPEN", "SAROP21-VVPG104-050:PLC_OPEN"],
        pvname_pump="SAROP21-VPIG103-060",
        name="vac_slit_mono", lazy=True, z_source=102.0, kind="vacuum",
        description="isolation valves + pump around the mono slit (real PVs; gauge PV unconfirmed)",
    )
    bl.add_component(
        SolidTargetDetectorPBPS, "SAROP21-PBPS103", use_calibration=False,
        diode_channels_raw={
            "up": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD1",
            "down": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD2",
            "left": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD0",
            "right": "SAROP21-PBPS103:Lnk9Ch0-PP_VAL_PD3",
        },
        name="mon_mono", lazy=True, z_source=103.0, kind="diagnostic",
    )
    bl.add_component(
        Pprm, "SAROP21-PPRM113", "SAROP21-PPRM113",
        bs_channels={
            "intensity": "SAROP21-PPRM113:intensity",
            "xpos": "SAROP21-PPRM113:x_fit_mean",
            "ypos": "SAROP21-PPRM113:y_fit_mean",
        },
        name="prof_mono", lazy=True, in_target=3, z_source=113.0, kind="profile",
    )
    bl.add_component(
        Pulsepick, Id="SAROP21-OPPI113",
        evronoff="SGE-CPCW-72-EVR0:FrontUnivOut15-Ena-SP",
        evrsrc="SGE-CPCW-72-EVR0:FrontUnivOut15-Src-SP",
        name="xp", lazy=True, z_source=113.0, kind="chopper", description="x-ray pulse picker",
    )
    bl.add_component(
        SafetyShutter, "SGE01-EPKT822:BST1_oeffnen", name="sshut_opt", lazy=True,
        z_source=115.0, kind="shutter", description="Bernina optics-hutch safety shutter",
    )
    bl.add_component(
        # with_devices=False: keep this to the single interlock PV -- the full
        # device inventory BerninaVacuum can also carry (see that class) would
        # duplicate `make_aramis_vacuum()` when this beamline is joined with it
        # via make_bernina_beamline(with_vacuum=True).
        BerninaVacuum, name="vacuum_interlock", with_devices=False, lazy=True,
        z_source=115.0, kind="valve",
        description="beamline-wide 'all valves open' PLC interlock (SAROP21-VVPG-0010, real PV)",
    )
    bl.add_zone_condition(
        "bernina_branch_selected",
        _bernina_branch_active_condition(),
        z_source=59.0, z_source_end=Z0_SOURCE_BERNINA_SAMPLE,
        description=(
            "Aramis beam routed to Bernina (by name, via AramisMode) with the "
            "front-end vacuum valve (SARFE10-SBST060:PLC_VCS_OPEN1) and photon "
            "shutter (SARFE10-OPSH059:PLC_OPEN) open -- see module docstring"
        ),
    )
    bl.add_component(
        Pprm, "SAROP21-PPRM133", "SAROP21-PPRM133", name="prof_opt", lazy=True, in_target=3,
        z_source=133.0, kind="profile",
    )
    bl.add_component(
        SolidTargetDetectorPBPS, "SAROP21-PBPS133", use_calibration=False,
        diode_channels_raw={
            "up": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD1",
            "down": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD2",
            "left": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD0",
            "right": "SAROP21-PBPS133:Lnk9Ch0-PP_VAL_PD3",
        },
        name="mon_opt", lazy=True, z_source=133.0, kind="diagnostic",
        pipeline_computation="SAROP21-PBPS133_proc",
    )
    bl.add_component(
        AttenuatorAramis, "SAROP21-OATT135", shutter=Proxy(lambda: bl.xp), set_limits=[],
        name="att", lazy=True, z_source=135.0, kind="attenuator", description="Bernina attenuator",
    )
    bl.add_component(
        Pprm, "SAROP21-PPRM138", "SAROP21-PPRM138",
        bs_channels={
            "intensity": "SAROP21-PPRM138:intensity",
            "xpos": "SAROP21-PPRM138:x_fit_mean",
            "ypos": "SAROP21-PPRM138:y_fit_mean",
        },
        name="prof_att", lazy=True, in_target=3, z_source=138.0, kind="profile",
    )
    bl.add_component(
        SlitPosWidth, "SAROP21-OAPU138", name="slit_att", lazy=True,
        z_source=138.0, kind="slit", description="slits behind the Bernina attenuator",
    )
    # Real valve pair found bracketing this area in Aramis.ui (no matching
    # gauge/pump macro found for this particular pair).
    bl.add_component(
        VacuumSection,
        pvname_valve=["SAROP21-VVPG136-210:PLC_OPEN", "SAROP21-VVPG138-220:PLC_OPEN"],
        name="vac_att", lazy=True, z_source=137.0, kind="vacuum",
        description="isolation valves around the attenuator/slit_att area (real PVs; no gauge/pump found)",
    )
    return bl


def make_bernina_experiment_hutch(name="bernina_experiment_hutch", xp=None):
    """Upstream diagnostics slits/attenuator -> KB mirrors -> sample ->
    downstream diagnostics, positioned via `z_sample` [mm] (the
    mm-precision, sample-referenced distances used by
    `eco.xoptics.kb_bernina.KBMirrorBernina` and `xoptics/applet_vis.ipynb`).

    Parameters
    ----------
    xp : Pulsepick, optional
        The pulse-picker from `make_bernina_front_end()`, used by `Att_usd`
        and `AttenuatorAramis` for their energy-dependent transmission
        calculations. Safe to leave as `None` for a layout-only draft.
    """
    from ..devices_general.motors import SmaractRecord
    from ..endstations.hexapod import HexapodSymmetrie
    from ..xoptics.att_usd import Att_usd
    from ..xoptics.kb_bernina import KBMirrorBernina
    from ..xoptics.slits import SlitBladesGeneral
    from ..xdiagnostics.dsd import DownstreamDiagnostic
    from ..xdiagnostics.intensity_monitors import SolidTargetDetectorBerninaUSD
    from ..xdiagnostics.profile_monitors import Pprm_dsd, ProfKbBernina

    bl = Beamline(
        name=name,
        z0_source=Z0_SOURCE_BERNINA_SAMPLE,
        source_name="Aramis undulator",
        energy_eV=DEFAULT_ENERGY_EV,
        description="Upstream diagnostics/KB mirrors, sample, downstream diagnostics.",
    )

    bl.add_component(
        HexapodSymmetrie, name="usd_table", lazy=True,
        z_sample=-1600.0, kind="stage",
        description="upstream-diagnostics hexapod table (carries slit_kb/att_usd)",
    )
    bl.add_component(
        SlitBladesGeneral,
        def_blade_up={"args": [SmaractRecord, "SARES20-MCS1:MOT_14"], "kwargs": {}},
        def_blade_down={"args": [SmaractRecord, "SARES20-MCS1:MOT_13"], "kwargs": {}},
        def_blade_left={"args": [SmaractRecord, "SARES20-MCS1:MOT_18"], "kwargs": {}},
        def_blade_right={"args": [SmaractRecord, "SARES20-MCS1:MOT_4"], "kwargs": {}},
        name="slit_kb", lazy=True, z_sample=-1850.0, kind="slit",
        description="slits upstream of the KB mirrors",
    )
    bl.add_component(
        SlitBladesGeneral,
        def_blade_up={"args": [SmaractRecord, "SARES20-MCS1:MOT_6"], "kwargs": {}},
        def_blade_down={"args": [SmaractRecord, "SARES20-MCS1:MOT_5"], "kwargs": {}},
        def_blade_left={"args": [SmaractRecord, "SARES20-MCS1:MOT_17"], "kwargs": {}},
        def_blade_right={"args": [SmaractRecord, "SARES20-MCS1:MOT_16"], "kwargs": {}},
        name="slit_cleanup", lazy=True, z_sample=-1800.0, kind="slit",
        description="cleanup slit, upstream diagnostics",
    )
    bl.add_component(
        Att_usd, name="att_usd", lazy=True, xp=xp,
        z_sample=-1420.0, kind="attenuator", description="upstream diagnostics attenuator",
    )
    bl.mark_position(
        "chamber_window_1", z_sample=-1945.0, kind="marker",
        description="sample chamber entrance window",
    )
    bl.add_component(
        KBMirrorBernina, "SAROP21-OKBV139", "SAROP21-OKBH140",
        usd_table=Proxy(lambda: bl.usd_table), diffractometer=None,
        name="kb", lazy=True, z_sample=-3000.0, kind="mirror",
        description="KB mirror pair (ver focus @ -3350 mm, hor focus @ -2600 mm)",
    )
    bl.mark_position(
        "kb_ver_focus", z_sample=-3350.0, kind="mirror", parent="kb",
        description="KB vertical mirror focus",
    )
    bl.mark_position(
        "kb_hor_focus", z_sample=-2600.0, kind="mirror", parent="kb",
        description="KB horizontal mirror focus",
    )
    bl.mark_position(
        "chamber_window_2", z_sample=-1330.0, kind="marker",
        description="sample chamber exit window",
    )
    bl.add_component(
        SolidTargetDetectorBerninaUSD, "SARES20-MCS1:MOT_12",
        channel_xpos="SARES21-PBPS141:XPOS",
        channel_ypos="SARES21-PBPS141:YPOS",
        channel_intensity="SARES21-PBPS141:INTENSITY",
        diode_channels_raw={
            "up": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD1",
            "down": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD2",
            "left": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD0",
            "right": "SARES21-PBPS141:Lnk9Ch0-PP_VAL_PD3",
        },
        pipeline_computation="SARES21-PBPS141_proc",
        name="mon_kb", lazy=True, z_sample=-1750.0, kind="diagnostic",
    )
    # Proxy, not a direct call: constructing ProfKbBernina here (rather than
    # when `bl.prof_kb` is first accessed) would make `add_component(...,
    # lazy=True)` below pointless -- the wrapped device would already be
    # built by the time anyone asks for it.
    _prof_kb_device = Proxy(lambda: ProfKbBernina(pvname_mirror="SARES20-MCS1:MOT_11", name="_prof_kb_device"))
    bl.add_component(
        ProfileMonitor,
        _prof_kb_device,
        name="prof_kb", lazy=True, z_sample=-1750.0, kind="profile",
        # ProfKbBernina.mirror_in is already the exact boolean this module's
        # `is_in_beam` convention asks for -- just relayed under that name.
        is_in_beam_test=lambda: bool(_prof_kb_device.mirror_in.get_current_value()),
        description=(
            "KB-target profile monitor; `image_getter` left unset (see "
            "`ProfileMonitor`) until wired to a live camera/pipeline"
        ),
    )
    # Real KB-tank valve+gauge from Aramis.ui's KB-mirror related-display
    # macros ("P=SAROP21-OKBH140, D=SAROP21-VMFR138-240" for the gauge; the
    # valve status widget "SARES21-VVPG140-230:PLC_OPEN"). No dedicated
    # beam-blocking stopper PV was found for this tank in that file.
    bl.add_component(
        VacuumSection,
        pvname_valve="SARES21-VVPG140-230:PLC_OPEN",
        pvname_gauge="SAROP21-VMFR138-240",
        name="vac_kb", lazy=True, z_sample=-3000.0, kind="vacuum",
        description="KB mirror tank valve + gauge (real PVs; gauge suffix unconfirmed)",
    )
    bl.mark_position(
        "sample", z_sample=-9.0, kind="sample", description="nominal sample interaction point",
    )
    bl.add_component(
        DownstreamDiagnostic, name="dsd_table", lazy=True,
        z_sample=3147.5, kind="stage", description="downstream diagnostics translation stage",
    )
    _prof_dsd_device = Proxy(
        lambda: Pprm_dsd(pvname="SARES20-DSDPPRM", pvname_camera="SARES20-PROF146-M1", name="_prof_dsd_device")
    )
    bl.add_component(
        ProfileMonitor,
        _prof_dsd_device,
        name="prof_dsd", lazy=True, z_sample=3725.0, kind="profile",
        is_in_beam_test=lambda: _prof_dsd_device.target.get_current_value() == 1,
        description="downstream diagnostics profile monitor",
    )

    return bl


def make_bernina_beamline(name="beamline", with_vacuum=False):
    """Convenience factory: build both sections and concatenate them into
    one beamline, as `front_end + experiment_hutch`, renamed to `name`.

    Meant to be registered as a lazy namespace component in
    `eco.bernina.bernina`, e.g.::

        namespace.append_obj(make_bernina_beamline, name="beamline", lazy=True)

    which is exactly why this accepts/applies `name` itself: `Namespace.
    append_obj` calls ``obj_maker(*args, name=name, **kwargs)`` whenever the
    factory's signature accepts a `name` parameter (see `eco.utilities.
    config.Namespace.append_obj`).

    `with_vacuum` (default False, opt-in): also merge in the Aramis Bernina-path
    vacuum model (see `eco.xoptics.aramis_vacuum`), so the beamline additionally
    knows every valve/gauge/pump along ``SARFE10 -> SAROP21 -> SARES21`` and
    their live state. Vacuum devices carry the ``valve``/``gauge``/``pump``
    kinds, so a view can list them in or leave them out via the ``kinds=``
    argument of `show_layout`/`position_table`/`plot_layout`, e.g.
    ``bl.show_layout(kinds=aramis_vacuum.NON_VACUUM_KINDS)`` to hide vacuum, or
    ``kinds=aramis_vacuum.VACUUM_KINDS`` to show only vacuum. Vacuum devices are
    built lazily, so this stays hardware-free until one is accessed.
    """
    front_end = make_bernina_front_end()
    # Proxy, not `front_end.xp` directly: the latter would force `xp` (a
    # `lazy=True` component) to resolve right here, before anyone asked
    # for it.
    experiment_hutch = make_bernina_experiment_hutch(xp=Proxy(lambda: front_end.xp))
    bl = front_end + experiment_hutch
    if with_vacuum:
        from .aramis_vacuum import make_aramis_vacuum

        bl = bl + make_aramis_vacuum(lazy=True)
    bl.name = name
    bl.alias.alias = name
    return bl


def demo_layout():
    """Hardware-free sanity check: reproduces the same component names and
    positions as `make_bernina_front_end`/`make_bernina_experiment_hutch`
    above, using `mark_position` only (no EPICS/control-system access), and
    exercises the position table, layout plot and beam-size fit/plot with a
    few synthetic profile-monitor measurements (loosely inspired by the
    `applet_vis.ipynb` defaults). Useful to sanity-check/tweak the beamline
    description itself, independently of whether the real devices are
    reachable.
    """
    front_end = Beamline(name="front_end_demo", z0_source=Z0_SOURCE_BERNINA_SAMPLE, energy_eV=DEFAULT_ENERGY_EV)
    for comp_name, z_source, kind in [
        ("pshut_und", 44.0, "shutter"),
        ("slit_und", 44.0, "slit"),
        ("prof_fe", 64.0, "profile"),
        ("att_fe", 53.0, "attenuator"),
        ("mon_und", 53.0, "diagnostic"),
        ("pshut_fe", 59.0, "shutter"),
        ("slit_switch", 92.0, "slit"),
        ("offset", 94.0, "mirror"),
        ("mono", 98.0, "mono"),
        ("slit_mono", 102.0, "slit"),
        ("prof_mono", 113.0, "profile"),
        ("xp", 113.0, "chopper"),
        ("sshut_opt", 115.0, "shutter"),
        ("vac_switch", 90.0, "vacuum"),
        ("vac_slit_mono", 102.0, "vacuum"),
        ("vac_att", 137.0, "vacuum"),
        ("prof_opt", 133.0, "profile"),
        ("att", 135.0, "attenuator"),
        ("prof_att", 138.0, "profile"),
        ("slit_att", 138.0, "slit"),
    ]:
        front_end.mark_position(comp_name, z_source=z_source, kind=kind)

    # Synthetic stand-in for `bernina_branch_selected` (see
    # make_bernina_front_end): a plain toggleable dict instead of live
    # EPICS, just to exercise `plot_layout`'s zone-highlighting offline.
    route_state = {"active": True}
    front_end.add_zone_condition(
        "bernina_branch_selected",
        lambda: route_state["active"],
        z_source=59.0, z_source_end=Z0_SOURCE_BERNINA_SAMPLE,
        description="synthetic stand-in for the real route+valve+shutter condition",
    )

    exp_hutch = Beamline(name="experiment_hutch_demo", z0_source=Z0_SOURCE_BERNINA_SAMPLE, energy_eV=DEFAULT_ENERGY_EV)
    for comp_name, z_sample, kind in [
        ("usd_table", -1600.0, "stage"),
        ("slit_kb", -1850.0, "slit"),
        ("att_usd", -1420.0, "attenuator"),
        ("chamber_window_1", -1945.0, "marker"),
        ("kb", -3000.0, "mirror"),
        ("chamber_window_2", -1330.0, "marker"),
        ("mon_kb", -1750.0, "diagnostic"),
        ("prof_kb", -1750.0, "profile"),
        ("vac_kb", -3000.0, "vacuum"),
        ("sample", -9.0, "sample"),
        ("dsd_table", 3147.5, "stage"),
        ("prof_dsd", 3725.0, "profile"),
    ]:
        exp_hutch.mark_position(comp_name, z_sample=z_sample, kind=kind)
    # ver/hor mirror foci nest under "kb" -- see beamline_assembly.diagram()'s
    # parent= unfolding, mirroring make_bernina_experiment_hutch above.
    exp_hutch.mark_position(
        "kb_ver_focus", z_sample=-3350.0, kind="mirror", parent="kb",
        description="KB vertical mirror focus",
    )
    exp_hutch.mark_position(
        "kb_hor_focus", z_sample=-2600.0, kind="mirror", parent="kb",
        description="KB horizontal mirror focus",
    )

    bl = front_end + exp_hutch
    bl.show_layout(ref="sample")
    bl.show_layout(ref="source")

    # Synthetic beam, just to exercise the fit/plot machinery end-to-end.
    truth = GaussianBeam(w0=0.010, z0=-1750.0, wavelength=energy_to_wavelength_mm(DEFAULT_ENERGY_EV))
    measurements = {
        dim: [(z, truth.fwhm(z) * 1e3) for z in (-3000.0, -1750.0, -9.0, 3725.0)]
        for dim in ("x", "y")
    }
    bl.plot_layout(ref="sample")
    bl.plot_beam_sizes(measurements=measurements, energy_eV=DEFAULT_ENERGY_EV)
    return bl


if __name__ == "__main__":
    import matplotlib.pyplot as plt

    demo_layout()
    plt.show()
