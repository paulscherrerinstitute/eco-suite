"""Generic framework for describing an x-ray beamline as a single Assembly.

An :class:`Beamline` is an ordered chain of components -- mirrors,
slits, attenuators, shutters, vacuum equipment, diagnostics, ... -- each
located at a well defined position along the photon beam, together with a
simple physical-optics model (Gaussian beam propagation) that can be fit to
profile-monitor measurements and plotted along the whole beamline.

Two position references are kept for every component:

- ``z_source`` [m]: absolute distance from the photon source (typically the
  undulator), increasing downstream. This is the frame in which the numeric
  part of most SwissFEL beamline component PVs is historically defined (e.g.
  ``SAROP21-OAPU102`` sits at roughly ``z_source = 102``), and is therefore
  the natural frame in which to compare or concatenate different beamline
  sections.
- ``z_sample`` [mm]: distance from the (nominal) sample/interaction point of
  *this* section, positive downstream, negative upstream. This is the frame
  used for precise optics calculations (see ``eco.xoptics.kb_bernina``) and
  for the beam-propagation/plotting logic below.

Only one of the two needs to be known for any given component: if the
section's own reference offset (``z0_source``, the source-position of this
section's local sample point) is set, the other coordinate is derived
automatically. Components (or pure markers, see ``mark_position``) may also
be registered with only one of the two coordinates known -- exactly the
messy, partially-documented state of a real beamline -- table/plot methods
simply skip whatever isn't available for a given reference frame.

Sections can be joined into a single, larger beamline with ``+``:

    full_beamline = optics_hutch + experiment_hutch

which behaves like one more `Beamline` (its Assembly tree nests both
sections as sub-assemblies, and its position table/plots merge both).

This module is a first draft meant for discussion and tweaking, not a
finished, validated model of any particular beamline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import colorama
import numpy as np

from ..elements.assembly import Assembly
from ..elements.protocols import Adjustable, Detector
from ..utilities.tables import format_table, section_row_styles

# --------------------------------------------------------------------------
# Gaussian beam optics (ported/generalised from xoptics/applet_vis.ipynb)
# --------------------------------------------------------------------------

#: FWHM = E2_RADIUS_TO_FWHM * w, where w is the 1/e^2 intensity radius.
E2_RADIUS_TO_FWHM = np.sqrt(2 * np.log(2))
#: FWHM = SIGMA_TO_FWHM * sigma, for a plain Gaussian intensity profile.
SIGMA_TO_FWHM = 2 * np.sqrt(2 * np.log(2))

#: h*c in keV*nm, used for the photon-energy <-> wavelength conversion.
_HC_KEV_NM = 1.23984193


def energy_to_wavelength_mm(energy_eV):
    """Photon energy [eV] -> vacuum wavelength [mm]."""
    return _HC_KEV_NM * 1e-6 / (energy_eV * 1e-3)


def fwhm_from_projection(profile, pixel_size_um=1.0):
    """Estimate a beam FWHM [um] from a 1D intensity projection (e.g. the
    column- or row-sum of a camera image), using the profile's second
    moment.

    This is a simple, robust-enough placeholder for an actual per-camera
    calibrated fit (or reading pre-computed ``..._fit_std`` channels from a
    camera-server pipeline, once such channels exist) -- good enough to
    exercise the fitting/plotting machinery below, not a substitute for
    validated beam diagnostics.
    """
    y = np.clip(np.asarray(profile, dtype=float) - np.percentile(profile, 5), 0, None)
    if y.sum() <= 0:
        return None
    x = np.arange(len(y))
    mean = np.sum(x * y) / np.sum(y)
    var = np.sum(y * (x - mean) ** 2) / np.sum(y)
    return np.sqrt(var) * SIGMA_TO_FWHM * pixel_size_um


@dataclass
class GaussianBeam:
    """A 1D Gaussian beam, parameterised the way it propagates through free
    space: ``w0`` is the waist (1/e^2 intensity radius) [mm] at focus
    position ``z0`` [mm], for light of the given ``wavelength`` [mm].
    """

    w0: float
    z0: float
    wavelength: float

    @property
    def zR(self):
        """Rayleigh range [mm]."""
        return np.pi * self.w0**2 / self.wavelength

    def w(self, z):
        """1/e^2 intensity radius [mm] at position(s) ``z`` [mm]."""
        z = np.asarray(z, dtype=float)
        return self.w0 * np.sqrt(1 + ((z - self.z0) / self.zR) ** 2)

    def fwhm(self, z):
        """FWHM [mm] at position(s) ``z`` [mm]."""
        return self.w(z) * E2_RADIUS_TO_FWHM

    @classmethod
    def from_single_measurement(cls, z, fwhm_um, z0, wavelength):
        """Analytic solve for w0 given exactly one (z [mm], FWHM [um])
        measurement and an assumed/known focus position ``z0`` [mm].

        Direct port of ``get_beam_waist``/``solve_for_w0`` from
        ``applet_vis.ipynb``: quadratic solve of
        ``w(z)^2 = w0^2 + (wavelength*(z-z0)/pi)^2 / w0^2`` for w0^2,
        keeping the larger root.

        Note the same ambiguity already present in the notebook: for a
        single (z, size) pair and an assumed z0, there are generically two
        positive roots for w0 (a small, strongly-diverging waist and a
        large, near-collimated one) that both reproduce the measurement.
        This always returns the larger one, which is not necessarily the
        physically correct branch -- prefer `from_fwhm_measurements` with
        two or more measurements whenever possible, since that removes the
        ambiguity.
        """
        w_target = (fwhm_um * 1e-3) / E2_RADIUS_TO_FWHM
        c = (wavelength * (z - z0) / np.pi) ** 2
        discriminant = w_target**4 - 4 * c
        if discriminant < 0:
            raise ValueError(
                "Measured size at z is below the diffraction limit for this "
                "z0/wavelength -- check z0 or the measurement."
            )
        w0_sq = (w_target**2 + np.sqrt(discriminant)) / 2
        return cls(w0=np.sqrt(w0_sq), z0=z0, wavelength=wavelength)

    @classmethod
    def from_fwhm_measurements(cls, z, fwhm_um, wavelength, w0_guess=None, z0_guess=None):
        """Least-squares fit of (w0, z0) to two or more (z [mm], FWHM [um])
        measurements at fixed wavelength.
        """
        from scipy.optimize import curve_fit

        z = np.asarray(z, dtype=float)
        fwhm_mm = np.asarray(fwhm_um, dtype=float) * 1e-3
        if len(z) < 2:
            raise ValueError(
                "Need at least 2 (z, fwhm) measurements to fit both w0 and z0; "
                "use GaussianBeam.from_single_measurement with a known/assumed "
                "z0 for a single measurement."
            )
        if z0_guess is None:
            z0_guess = z[np.argmin(fwhm_mm)]
        if w0_guess is None:
            w0_guess = max(fwhm_mm.min() / E2_RADIUS_TO_FWHM, 1e-6)

        def model(z, w0, z0):
            zr = np.pi * w0**2 / wavelength
            return w0 * np.sqrt(1 + ((z - z0) / zr) ** 2) * E2_RADIUS_TO_FWHM

        popt, _ = curve_fit(model, z, fwhm_mm, p0=[w0_guess, z0_guess], maxfev=20000)
        return cls(w0=popt[0], z0=popt[1], wavelength=wavelength)


class BeamSizeProbe:
    """Duck-typing protocol for components registered with
    ``kind="profile"``: any component exposing
    ``get_beam_fwhm() -> {"x": fwhm_um_or_None, "y": fwhm_um_or_None}`` can be
    polled automatically by :meth:`Beamline.measure_beam_sizes`. This
    class exists only for documentation/typing purposes; subclassing it is
    not required.
    """

    def get_beam_fwhm(self):
        raise NotImplementedError


# --------------------------------------------------------------------------
# Proposed convention for "in/out" (insertable/movable) components
#
# Across the codebase, insertable components (profile-monitor targets, KB
# alignment mirrors, the pulse picker paddle, ...) already agree on the
# *action* names `movein()`/`moveout()` -- worth keeping, it's muscle memory.
# What's missing/inconsistent is the *readback*: some check a `.target`
# Adjustable against an `in_target` value, some (`ProfKbBernina.mirror_in`)
# already expose a proper boolean `AdjustableVirtual`, most expose nothing
# queryable at all. `xoptics/Aramis.ui` shows the same need on the display
# side: almost every "is this thing in the way" indicator there is a
# `visibilityCalc` comparing a position/state readback against a reference
# value (e.g. `caPolyLine_9`'s `"( A = 0 )"` on a screen-position channel).
#
# Proposed house convention: every insertable/movable component should
# additionally expose
#
#     component.is_in_beam.get_current_value() -> bool
#
# as a first-class, read-only `Detector` (not a plain method) under this
# exact name, so it shows up in `get_display_str()`/status tables like any
# other channel, and so generic code (this module's `plot_layout`/
# `SectionCondition`, but also elog status reports etc.) can rely on one
# name across every insertable device instead of re-deriving it from
# whatever attribute that particular device happens to expose. `add_is_in_beam`
# below is the one-line building block for wiring this up, either for a new
# device (see `SafetyStopper`) or non-invasively on top of an existing one
# (see `ProfileMonitor`).
# --------------------------------------------------------------------------


def add_is_in_beam(assembly, is_in_beam_test, name="is_in_beam", unit=None):
    """Attach a read-only boolean ``is_in_beam`` `Detector` to `assembly`,
    computed by the zero-argument callable `is_in_beam_test` (typically a
    closure comparing some existing Adjustable's current value against a
    reference, e.g. ``lambda: device.target.get_current_value() == device.in_target``).
    """
    from ..elements.detector import DetectorVirtual

    assembly._append(
        DetectorVirtual, [], lambda: bool(is_in_beam_test()), name=name, unit=unit, is_setting=False
    )
    return assembly.__dict__[name]


class ProfileMonitor(Assembly):
    """Thin, non-invasive wrapper that adds a best-effort
    :meth:`get_beam_fwhm` (satisfying `BeamSizeProbe`) around an existing
    camera-based profile-monitor device (e.g. ``Pprm``, ``ProfKbBernina``,
    ``Pprm_dsd``), without modifying that device.

    ``image_getter`` is a no-arg callable returning the current 2D camera
    image as an array; wire it up to whatever the wrapped device's camera
    object exposes (e.g. a bs-stream client's last image). Left as `None`
    until such a live source is confirmed on site -- `get_beam_fwhm` will
    then raise instead of silently returning nonsense.

    ``is_in_beam_test``, if given, is wired up via `add_is_in_beam` (see
    above) -- e.g. ``lambda: device.mirror_in.get_current_value()`` for a
    `ProfKbBernina`, or ``lambda: device.target.get_current_value() ==
    device.in_target`` for a plain `Pprm`.
    """

    def __init__(self, device, name=None, pixel_size_um=1.0, image_getter=None, is_in_beam_test=None):
        super().__init__(name=name)
        # If `device` is a `lazy_object_proxy.Proxy` (e.g. so it stays
        # unconstructed until this whole `ProfileMonitor` is, via
        # `Beamline.add_component(..., lazy=True)`), unwrap it before
        # `_append`: `_append`/`StatusCollection` weakref the object they're
        # given, and a still-wrapped `Proxy` cannot itself be weakly
        # referenced (the resolved, real object can).
        device = getattr(device, "__wrapped__", device)
        self._append(device, name="device", is_display="recursive")
        self.pixel_size_um = pixel_size_um
        self.image_getter = image_getter
        if is_in_beam_test is not None:
            add_is_in_beam(self, is_in_beam_test)

    def get_beam_fwhm(self):
        if self.image_getter is None:
            raise NotImplementedError(
                f"No image_getter configured for '{self.name}'; wire one up to "
                "this monitor's camera/pipeline (or to precomputed fit-width "
                "channels, once available) to enable automatic beam-size "
                "measurement."
            )
        image = np.asarray(self.image_getter())
        return {
            "x": fwhm_from_projection(image.sum(axis=0), self.pixel_size_um),
            "y": fwhm_from_projection(image.sum(axis=1), self.pixel_size_um),
        }


# --------------------------------------------------------------------------
# Generic vacuum-section building blocks
#
# Refined against the real PSI/SwissFEL synoptic `xoptics/Aramis.ui` (a
# caQtDM screen): individual valves along the beamline (e.g.
# "SAROP21-VVPG098-040:PLC_OPEN") are PLC/interlock-automatic and expose
# *only* a status readback -- there is no per-valve remote open/close
# command anywhere in that screen. The only valve with actual "Open
# Valve"/"Close Valve" buttons (writing to a separate ":PLC_OPEN_F"/
# ":PLC_CLOSE_F" pair of *command* PVs, distinct from the ":PLC_OPEN"
# *status* PV) is a single beamline-wide master valve
# ("SAROP21-VVPG-0010"). `Valve` below models that asymmetry directly
# instead of pretending every valve is freely settable.
#
# The macro strings driving that screen's related-displays also spell out
# the real station topology, e.g.:
#   NAME=OAPU092_ADC, P=SAROP21-OAPU092, PUMP=SAROP11-VPIG090-030,
#   GAUGE=SAROP11-VMFR090-030, VALVE1=SAROP11-VVPG087-030, VALVE2=SAROP11-VVPG091-010
# i.e. one pump + one gauge bracketed by *two* isolation valves (one on
# each side), not just one -- `VacuumSection` accepts a list of valves.
# --------------------------------------------------------------------------


class Valve(Assembly):
    """A single vacuum valve.

    Most beamline valves are PLC/interlock-automatic: they only expose an
    open/closed status readback (``pvname_status``, e.g. ending in
    ``:PLC_OPEN``), with no remote command of their own. A handful of
    "master" valves additionally have separate, momentary command PVs
    (``pvname_open_cmd``/``pvname_close_cmd``, e.g. ``:PLC_OPEN_F``/
    ``:PLC_CLOSE_F`` -- deliberately *not* the same PV as the status
    readback). Leave those `None` for a plain status-only valve; `open()`/
    `close()` then raise instead of silently doing nothing.
    """

    def __init__(self, pvname_status, name=None, pvname_open_cmd=None, pvname_close_cmd=None):
        super().__init__(name=name)
        self.pvname_status = pvname_status
        from ..epics_utils.adjustable import AdjustablePvEnum
        from ..epics_utils.detector import DetectorPvEnum

        self._append(DetectorPvEnum, pvname_status, name="is_open", is_setting=False)
        if pvname_open_cmd:
            self._append(
                AdjustablePvEnum, pvname_open_cmd, name="_open_cmd", is_setting=False, is_display=False
            )
        if pvname_close_cmd:
            self._append(
                AdjustablePvEnum, pvname_close_cmd, name="_close_cmd", is_setting=False, is_display=False
            )

    def open(self):
        if not hasattr(self, "_open_cmd"):
            raise NotImplementedError(
                f"'{self.name}' has no remote open command (status-only, "
                "PLC/interlock-automatic valve)."
            )
        self._open_cmd.set_target_value(1)

    def close(self):
        if not hasattr(self, "_close_cmd"):
            raise NotImplementedError(
                f"'{self.name}' has no remote close command (status-only, "
                "PLC/interlock-automatic valve)."
            )
        self._close_cmd.set_target_value(1)


class Gauge(Assembly):
    """A vacuum gauge with a pressure readback."""

    def __init__(self, pvname, name=None, unit="mbar"):
        super().__init__(name=name)
        self.pvname = pvname
        from ..epics_utils.detector import DetectorPvData

        self._append(DetectorPvData, pvname, name="pressure", unit=unit, is_setting=False)


class Pump(Assembly):
    """A vacuum pump (ion pump, turbo, ...) with on/off state and an
    optional speed/current readback."""

    def __init__(self, pvname, name=None, pvname_speed=None):
        super().__init__(name=name)
        self.pvname = pvname
        from ..epics_utils.adjustable import AdjustablePvEnum
        from ..epics_utils.detector import DetectorPvData

        self._append(AdjustablePvEnum, pvname, name="state", is_setting=True)
        if pvname_speed:
            self._append(DetectorPvData, pvname_speed, name="speed", is_setting=False)


class SafetyStopper(Assembly):
    """A beam-blocking safety stopper/paddle (as opposed to a
    `eco.xoptics.shutters.SafetyShutter`, which gates the whole hutch).

    Exposes `is_in_beam` (see the in/out convention discussed in the module
    docstring) alongside the usual `movein()`/`moveout()` action methods.
    """

    def __init__(self, pvname, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        from ..epics_utils.adjustable import AdjustablePvEnum
        from ..elements.adjustable import AdjustableTrigger

        self._append(AdjustablePvEnum, pvname, name="_state", is_setting=True, is_display=False)
        add_is_in_beam(self, lambda: bool(self._state.get_current_value()))
        self._append(AdjustableTrigger, self.movein, name="movein", button_label="Move In")
        self._append(
            AdjustableTrigger, self.moveout, name="moveout", button_label="Move Out"
        )

    def movein(self):
        self._state.set_target_value(1)

    def moveout(self):
        self._state.set_target_value(0)


class VacuumSection(Assembly):
    """Groups whichever of valve(s)/gauge/pump(s)/stopper are physically
    co-located at one point of the beamline. Pass only the PVs that apply;
    e.g. a pure gauge station only needs ``pvname_gauge``.

    ``pvname_valve`` accepts either a single status PV or a list of them
    (real stations are commonly bracketed by two isolation valves, one on
    each side of the pump/gauge -- see module note above); they show up as
    ``valve``/``valve_2``/... in the display tree.
    """

    def __init__(
        self,
        name=None,
        pvname_valve=None,
        pvname_gauge=None,
        pvname_pump=None,
        pvname_stopper=None,
    ):
        super().__init__(name=name)
        if pvname_valve:
            valve_pvs = [pvname_valve] if isinstance(pvname_valve, str) else list(pvname_valve)
            for n, pv in enumerate(valve_pvs):
                vname = "valve" if n == 0 else f"valve_{n + 1}"
                self._append(Valve, pv, name=vname, is_display="recursive")
        if pvname_gauge:
            self._append(Gauge, pvname_gauge, name="gauge", is_display="recursive")
        if pvname_pump:
            self._append(Pump, pvname_pump, name="pump", is_display="recursive")
        if pvname_stopper:
            self._append(SafetyStopper, pvname_stopper, name="stopper", is_display="recursive")


# --------------------------------------------------------------------------
# Robustness: a component whose real constructor failed
# --------------------------------------------------------------------------


class DummyComponent(Assembly):
    """Stand-in for a component whose real constructor raised (missing
    hardware/dependency/network), so one bad PV doesn't take down a
    beamline with dozens of components. Used automatically by
    `Beamline.add_component` unless `strict=True` is passed.
    """

    def __init__(self, name=None, error=None):
        super().__init__(name=name)
        self.error = error

    def get_current_value(self):
        return None

    def __repr__(self):
        return f"<DummyComponent '{self.name}': {self.error!r}>"


# --------------------------------------------------------------------------
# The beamline assembly itself
# --------------------------------------------------------------------------


@dataclass
class _Position:
    name: str
    z_source: Optional[float]  # m, downstream of the source
    z_sample: Optional[float]  # mm, downstream of this section's sample point
    kind: str
    description: str = ""
    #: end of the span, for kind="zone"-style entries that cover a
    #: stretch of beamline rather than a point; `None` for point components.
    z_source_end: Optional[float] = None
    z_sample_end: Optional[float] = None
    #: name of another registered position this one nests under (e.g. a KB
    #: mirror pair's individual focus points nest under the pair's own
    #: entry) -- `None` for a top-level position. See `position_table`/
    #: `diagram` for how nesting affects ordering/display; purely
    #: presentational, doesn't change z_source/z_sample derivation.
    parent: Optional[str] = None


class Beamline(Assembly):
    """An Assembly representing an ordered chain of beamline components,
    each with a known position along the photon beam. See module docstring
    for the two position references kept (``z_source`` / ``z_sample``).
    """

    #: default colors for `plot_layout`/`plot_beam_sizes`, by `kind`.
    KIND_COLORS = {
        "source": "#444444",
        "mirror": "#1f77b4",
        "mono": "#1f77b4",
        "slit": "#2ca02c",
        "attenuator": "#bcbd22",
        "shutter": "#d62728",
        "stopper": "#d62728",
        "valve": "#8c564b",
        "gauge": "#9467bd",
        "pump": "#7f7f7f",
        "vacuum": "#8c564b",
        "profile": "#17becf",
        "diagnostic": "#17becf",
        "chopper": "#e377c2",
        "stage": "#7f7f7f",
        "sample": "#000000",
        "marker": "#999999",
        "zone": "#f5e100",
        "optic": "#1f77b4",
    }

    #: static glyph per `kind`, used by `diagram()`/`__repr__` for
    #: components whose live open/closed state either doesn't apply or
    #: can't be read (see `BLOCKING_KINDS`/`_read_open_state` for those that
    #: do apply).
    KIND_GLYPHS = {
        "mirror": "\U0001d20e",  # 𝈎
        "mono": "⸗",  # ⸗
        "optic": "⦈",  # ⦈
        "slit": "⌗",  # ⌗
        "attenuator": "\U0001d14d",  # 𝅍
        "profile": "⦿",  # ⦿
        "diagnostic": "◇",
        "timing": "⏱",  # ⏱
        "xspect": "🌈",  # 🌈
        "chopper": "⚈",  # ⚈
        "shutter": "⬒",  # ⬒ (sshut, pshut, xp)
        "stage": "▭",  # ▭
        "valve": "⋈",  # ⋈
        "vacuum": "🌀",  # 🌀
        "gauge": "Ⓟ",  # Ⓟ
        "pump": "\U0001d546",  # 𝕆
        "ipm": "⟴",  # ⟴
        "sample": "💎",  # 💎
        "marker": "·",  # ·
    }

    #: `kind`s whose live open/closed state is worth trying to read (see
    #: `_read_open_state`) for the beam-transmission column in `diagram()`.
    BLOCKING_KINDS = {"shutter", "stopper", "valve"}

    #: `diagram()` colour for a non-`BLOCKING_KINDS` component currently read
    #: as "in" the beam (`is_in_beam=True`, see the module-docstring in/out
    #: convention) -- distinct from `colorama.Fore.RED`, which `diagram()`
    #: reserves for an actual `BLOCKING_KINDS` closure (kills beam downstream).
    #: No plain `colorama.Fore` name is orange; this is a raw ANSI 256-colour
    #: escape (208, a standard "orange").
    IN_BEAM_COLOR = "\x1b[38;5;208m"

    #: `diagram()` row-background palette, one (base, alt) pair per section --
    #: see `eco.utilities.tables.section_row_styles` for how it's applied and
    #: why the colours are built the way they are. `None` (the default) means
    #: "use that module's shared `DEFAULT_SECTION_PALETTE`"; override on a
    #: subclass for a different look.
    SECTION_BG_SHADES = None

    def __init__(
        self,
        name=None,
        z0_source=None,
        source_name="undulator",
        energy_eV=None,
        description="",
    ):
        """
        Parameters
        ----------
        z0_source : float, optional
            Position [m] of this section's local sample/reference point, in
            the frame of ``source_name``. If given, `add_component`/
            `mark_position` can be called with either `z_source` or
            `z_sample` and will derive the other.
        source_name : str
            Label of the upstream reference point (e.g. "undulator"), for
            display purposes only.
        energy_eV : float, optional
            Nominal photon energy, used as the default for beam-size fits.
        """
        super().__init__(name=name)
        self.z0_source = z0_source
        self.source_name = source_name
        self.energy_eV = energy_eV
        self.description = description
        self._positions = {}
        self._sections = []  # child Beamline instances, once joined via `+`
        self._lazy_components = {}  # name -> pending-construction spec, see add_component(lazy=True)

    def __getattr__(self, name):
        # Only reached when normal attribute lookup already failed, i.e.
        # `name` is not yet a real attribute -- exactly the case for a
        # not-yet-resolved `add_component(..., lazy=True)` component. Uses
        # `__dict__.get` (not `self._lazy_components`) to avoid recursing
        # back into `__getattr__` if called before `__init__` has run.
        lazy = self.__dict__.get("_lazy_components", {})
        if name in lazy:
            spec = lazy.pop(name)
            self._construct(
                spec["obj"], spec["args"], spec["kwargs"], name,
                spec["is_setting"], spec["is_display"], spec["is_status"],
                spec["overwrite"], spec["strict"],
            )
            return self.__dict__[name]
        raise AttributeError(
            f"'{type(self).__name__}' object ('{self.name}') has no attribute {name!r}"
        )

    # ---- position bookkeeping -----------------------------------------

    def _to_source(self, z_sample):
        if z_sample is None or self.z0_source is None:
            return None
        return self.z0_source + z_sample / 1000.0

    def _to_sample(self, z_source):
        if z_source is None or self.z0_source is None:
            return None
        return (z_source - self.z0_source) * 1000.0

    def _register(
        self, name, z_source, z_sample, kind, description, z_source_end=None, z_sample_end=None,
        parent=None,
    ):
        if parent is not None and parent not in self._positions:
            raise KeyError(
                f"parent='{parent}' for '{name}' is not a registered position of "
                f"'{self.name}' -- register it first (add_component/mark_position)."
            )
        if z_source is None and z_sample is not None:
            z_source = self._to_source(z_sample)
        elif z_sample is None and z_source is not None:
            z_sample = self._to_sample(z_source)
        if z_source_end is None and z_sample_end is not None:
            z_source_end = self._to_source(z_sample_end)
        elif z_sample_end is None and z_source_end is not None:
            z_sample_end = self._to_sample(z_source_end)
        self._positions[name] = _Position(
            name=name,
            z_source=z_source,
            z_sample=z_sample,
            kind=kind,
            description=description,
            z_source_end=z_source_end,
            z_sample_end=z_sample_end,
            parent=parent,
        )

    def _construct(self, obj, args, kwargs, name, is_setting, is_display, is_status, overwrite, strict):
        """Build `obj(*args, **kwargs, name=name)` and `_append` it; on
        failure, replace it with a `DummyComponent` instead of raising,
        unless `strict=True`. Shared by the eager and (on first access) the
        `lazy=True` path of `add_component`."""
        try:
            self._append(
                obj, *args, name=name, is_setting=is_setting, is_display=is_display,
                is_status=is_status, overwrite=overwrite, **kwargs,
            )
        except Exception as e:
            if strict:
                raise
            print(f"'{name}' failed to initialize ({e!r}); using a DummyComponent instead.")
            self._append(
                DummyComponent(name=name, error=e), name=name, is_setting=is_setting,
                is_display=is_display, is_status=is_status, overwrite=overwrite,
            )
        return self.__dict__[name]

    def add_component(
        self,
        obj,
        *args,
        name=None,
        z_source=None,
        z_sample=None,
        z_source_end=None,
        z_sample_end=None,
        kind="optic",
        description="",
        parent=None,
        is_setting=False,
        is_display=True,
        is_status=True,
        overwrite=False,
        lazy=False,
        strict=False,
        **kwargs,
    ):
        """Append a control-system component to this beamline (via
        `Assembly._append`) and register its position.

        Give either `z_source` [m, from `source_name`] or `z_sample` [mm,
        from this section's sample point] (or both, if they disagree and
        that's worth recording as-is -- see module docstring); `kind` tags
        the component for the table/plot methods, e.g. "mirror", "slit",
        "attenuator", "shutter", "valve", "gauge", "pump", "stopper",
        "profile", "diagnostic", "chopper", "sample", "zone". A "zone" (see
        `add_zone_condition`) covers a stretch of beamline rather than a
        point -- also give `z_source_end`/`z_sample_end`.

        `parent`: optional name of another already-registered position (in
        this same beamline) that this one is physically/logically part of
        -- e.g. a KB mirror pair's individual focus points nesting under the
        pair's own entry. Purely presentational: `diagram()` lists it
        indented directly under `parent` instead of at its own place in the
        global z order (see `position_table`); everything else (z lookup,
        live status, ...) works exactly like a top-level position.

        `lazy` (default `False`, opt-in only -- this is specific to
        `Beamline.add_component`, it does *not* change the shared
        `Assembly._append` used everywhere else): defer actually building
        `obj(...)` until this component's name is first accessed on this
        beamline (e.g. `bl.mono`, or anything in this module that resolves a
        component by name -- `diagram()`, `_find_component()`, ...). Its
        position is registered immediately either way, so the layout table/
        diagram lists it right away; only the (potentially slow, EPICS-
        touching) construction is deferred. Useful once a beamline has
        enough components that building them all up front is slow.

        `strict` (default `False`): if a component's constructor raises, it
        is replaced with a `DummyComponent` carrying the error instead of
        propagating it, so one missing/unreachable device doesn't take the
        whole beamline down. Pass `strict=True` to get the original
        behavior (let it raise) for a specific component.
        """
        self._register(
            name, z_source, z_sample, kind, description, z_source_end, z_sample_end,
            parent=parent,
        )
        if lazy:
            self._lazy_components[name] = dict(
                obj=obj, args=args, kwargs=kwargs, is_setting=is_setting, is_display=is_display,
                is_status=is_status, overwrite=overwrite, strict=strict,
            )
            from lazy_object_proxy import Proxy

            return Proxy(lambda: getattr(self, name))
        return self._construct(obj, args, kwargs, name, is_setting, is_display, is_status, overwrite, strict)

    def add_zone_condition(
        self,
        name,
        condition,
        z_source=None,
        z_sample=None,
        z_source_end=None,
        z_sample_end=None,
        description="",
    ):
        """Register a live boolean "is this stretch of beamline currently
        active/carrying beam" condition, spanning [z, z_end], as a
        `kind="zone"` entry. ("Zone" to avoid clashing with the unrelated
        `Beamline` "sections" joined via `+`.)

        `condition` is a zero-arg callable returning bool -- typically an AND
        of a route/mode selector plus the valves/shutters along that path,
        directly modelled on the `visibilityCalc` rules in `Aramis.ui` that
        light up a beam-path segment yellow (e.g. ``"((A=1)&&(B=1))&&(C=1)"``
        on route/valve/shutter channels). See `plot_layout`/`show_layout` for
        how the live value is displayed.
        """
        from ..elements.detector import DetectorVirtual

        self._append(DetectorVirtual, [], lambda: bool(condition()), name=name, is_setting=False)
        self._register(
            name, z_source, z_sample, "zone", description, z_source_end, z_sample_end
        )
        return self.__dict__[name]

    def mark_position(
        self, name, z_source=None, z_sample=None, kind="marker", description="", parent=None
    ):
        """Record a position of interest that has no control-system
        component of its own (a chamber window, a nominal focus target, the
        sample position itself, ...). `parent`: see `add_component`."""
        self._register(name, z_source, z_sample, kind, description, parent=parent)

    def __add__(self, other):
        if not isinstance(other, Beamline):
            return NotImplemented
        name = f"{self.name}_{other.name}" if self.name and other.name else None
        joined = Beamline(
            name=name,
            z0_source=other.z0_source if other.z0_source is not None else self.z0_source,
            source_name=self.source_name,
            energy_eV=other.energy_eV or self.energy_eV,
        )
        for section in (self, other):
            for sub in section._sections or [section]:
                joined._append(sub, name=sub.name, is_display="recursive")
                joined._sections.append(sub)
        return joined

    def _iter_positions(self):
        """Yield (owning_section, position) for this beamline and, if it is
        itself the result of joining sections, all of its sections."""
        for section in self._sections or [self]:
            for pos in section._positions.values():
                yield section, pos

    # ---- layout table / plot -------------------------------------------

    def position_table(self, ref="sample", kinds=None, sort=True):
        """Return rows of (name, kind, z, z_end, section_name, description)
        for `ref` in {"sample", "source"}. `z_end` is `None` except for
        `kind="zone"` spans (see `add_zone_condition`)."""
        rows = []
        for section, pos in self._iter_positions():
            if kinds is not None and pos.kind not in kinds:
                continue
            if ref == "sample":
                z, z_end = pos.z_sample, pos.z_sample_end
            else:
                z, z_end = pos.z_source, pos.z_source_end
            rows.append((pos.name, pos.kind, z, z_end, section.name, pos.description))
        if sort:
            rows.sort(key=lambda r: (r[2] is None, r[2]))
        return rows

    def _diagram_rows(self, ref="source"):
        """Like `position_table`, but with every position carrying a
        `parent=` (see `add_component`/`mark_position`) spliced in directly
        after its parent instead of sitting wherever pure z order would put
        it, each row additionally carrying its nesting `depth` (0 for a
        top-level position). Used by `diagram()` to "unfold" a component's
        own beamline-character sub-positions -- e.g. a KB mirror pair's
        individual ver/hor focus points -- directly under that component's
        row. `kind="zone"` spans are excluded (nesting doesn't apply to
        them; `diagram()` lists those separately via `position_table`).

        Returns rows of (name, kind, z, z_end, section_name, description,
        depth), z in `ref` {"sample", "source"} as in `position_table`.
        """
        positions = {
            pos.name: (section, pos)
            for section, pos in self._iter_positions()
            if pos.kind != "zone"
        }
        children = {}
        for name, (_section, pos) in positions.items():
            if pos.parent is not None and pos.parent in positions:
                children.setdefault(pos.parent, []).append(name)

        def z_of(name):
            _section, pos = positions[name]
            return pos.z_sample if ref == "sample" else pos.z_source

        def sort_key(name):
            z = z_of(name)
            return (z is None, z)

        rows = []

        def emit(name, depth):
            section, pos = positions[name]
            if ref == "sample":
                z, z_end = pos.z_sample, pos.z_sample_end
            else:
                z, z_end = pos.z_source, pos.z_source_end
            rows.append((name, pos.kind, z, z_end, section.name, pos.description, depth))
            for child in sorted(children.get(name, []), key=sort_key):
                emit(child, depth + 1)

        top_level = sorted(
            (name for name, (_section, pos) in positions.items() if pos.parent is None),
            key=sort_key,
        )
        for name in top_level:
            emit(name, 0)
        return rows

    def _find_component(self, name):
        """Return the actual component object registered as `name` (in this
        beamline or any of its joined sections), or `None`. Uses `getattr`
        rather than a raw `__dict__` lookup so a not-yet-resolved
        `add_component(..., lazy=True)` component gets constructed here."""
        for section, pos in self._iter_positions():
            if pos.name == name:
                try:
                    return getattr(section, name)
                except AttributeError:
                    return None
        return None

    def _component_value_str(self, component):
        """Best-effort ``component.get_current_value()``, for `show_layout`'s
        and `diagram()`'s status columns; blank if unavailable (no live
        connection, not a Detector, a composite with no top-level value of
        its own, ...) -- mirrors `Assembly.get_display_str`'s "if provided"
        handling of a sub-assembly with no `get_current_value`."""
        get_current_value = getattr(component, "get_current_value", None)
        if get_current_value is None:
            return ""
        try:
            return str(get_current_value())
        except Exception:
            return "?"

    def _live_value_str(self, name):
        """`_component_value_str`, resolving `name` across all sections
        first (see `_find_component`)."""
        return self._component_value_str(self._find_component(name))

    def _type_char_str(self, component):
        """Adjustable/Detector/descend-into-sub-assembly glyphs for a
        component, matching `Assembly.get_display_str`'s type column
        (✏️ adjustable, 👁️ detector, ↳ has its own `status_collection`)."""
        if component is None or isinstance(component, DummyComponent):
            return ""
        typechar = ""
        if isinstance(component, Adjustable):
            typechar += "✏️"
        elif isinstance(component, Detector):
            typechar += "👁️"
        if hasattr(component, "status_collection"):
            typechar += " ↳"
        return typechar

    def show_layout(self, ref="sample", kinds=None):
        """Print (and return) a table of registered component positions,
        including a best-effort live value for `kind="zone"` conditions
        (see `add_zone_condition`)."""
        unit = "mm" if ref == "sample" else "m"
        rows = self.position_table(ref=ref, kinds=kinds)

        def fmt_z(z, z_end):
            if z is None:
                return ""
            if z_end is None:
                return f"{z:.3f}"
            return f"{z:.3f} .. {z_end:.3f}"

        s = format_table(
            [
                [n, k, fmt_z(z, z_end), (self._live_value_str(n) if k == "zone" else ""), sec, d]
                for n, k, z, z_end, sec, d in rows
            ],
            headers=["name", "kind", f"z_{ref} [{unit}]", "active?", "section", "description"],
        )
        print(s)
        return s

    def get_z(self, name, ref="sample"):
        for _, pos in self._iter_positions():
            if pos.name == name:
                return pos.z_sample if ref == "sample" else pos.z_source
        raise KeyError(f"No registered position for component '{name}'")

    #: color for a `kind="zone"` span currently evaluating True/False,
    #: matching the yellow-highlighted-beam-path convention seen in
    #: `Aramis.ui` (a lit, active path segment vs. a plain gray one).
    ZONE_ACTIVE_COLOR = "#f5e100"
    ZONE_INACTIVE_COLOR = "#cccccc"

    def plot_layout(self, ref="sample", kinds=None, ax=None, figsize=(12, 3)):
        """Draw a schematic of registered component positions along z:
        point components as colored vertical lines with rotated labels (as
        in the notebook's z-probe rows), `kind="zone"` spans (see
        `add_zone_condition`) as a shaded band colored by their current
        live value -- yellow if active, gray if not, hatched red if the
        condition itself couldn't be evaluated (e.g. no EPICS connection).
        """
        import matplotlib.pyplot as plt

        rows = [r for r in self.position_table(ref=ref, kinds=kinds) if r[2] is not None]
        points = [r for r in rows if r[3] is None]
        spans = [r for r in rows if r[3] is not None]

        if ax is None:
            _, ax = plt.subplots(figsize=figsize)

        for name, _kind, z, z_end, _section, _desc in spans:
            component = self._find_component(name)
            try:
                active = bool(component.get_current_value())
                color = self.ZONE_ACTIVE_COLOR if active else self.ZONE_INACTIVE_COLOR
                hatch = None
            except Exception:
                color = "#d62728"
                hatch = "//"
            ax.axvspan(z, z_end, color=color, alpha=0.35, hatch=hatch, zorder=0)
            ax.text((z + z_end) / 2, 1.3, name, ha="center", fontsize=8, color="#333333")

        for name, kind, z, _z_end, _section, _desc in points:
            color = self.KIND_COLORS.get(kind, "#333333")
            ax.axvline(z, color=color, lw=1.5, alpha=0.7)
            ax.text(z, 1.0, name, rotation=90, va="bottom", ha="center", fontsize=8, color=color)

        ax.set_ylim(0, 1.4)
        ax.set_yticks([])
        ax.set_xlabel(f"z_{ref} [{'mm' if ref == 'sample' else 'm'}]")
        ax.set_title(self.name or "beamline layout")
        return ax

    # ---- beam size measurement / fit / plot ------------------------------

    def measure_beam_sizes(self, kind="profile"):
        """Poll every registered component of the given `kind` for a beam
        size measurement (see `BeamSizeProbe`).

        Returns a list of (z_sample [mm], name, fwhm_x [um], fwhm_y [um]).
        """
        out = []
        for section, pos in self._iter_positions():
            if pos.kind != kind:
                continue
            component = section.__dict__.get(pos.name)
            get_fwhm = getattr(component, "get_beam_fwhm", None)
            if get_fwhm is None:
                continue
            try:
                fwhm = get_fwhm()
            except Exception as e:
                print(f"Could not get beam size from '{pos.name}': {e}")
                continue
            out.append((pos.z_sample, pos.name, fwhm.get("x"), fwhm.get("y")))
        return out

    def fit_beam_size(self, measurements=None, dim="x", energy_eV=None, kind="profile"):
        """Fit a `GaussianBeam` to (z_sample [mm], fwhm [um]) measurements
        for one transverse dimension.

        `measurements`: optional explicit list of (z_sample, fwhm); if
        omitted, `measure_beam_sizes(kind=kind)` is polled for live data.
        """
        energy_eV = energy_eV or self.energy_eV
        if energy_eV is None:
            raise ValueError(
                "energy_eV must be given (or set on the beamline) to fit a beam waist."
            )
        wavelength = energy_to_wavelength_mm(energy_eV)

        if measurements is None:
            measurements = [
                (z, (fx if dim == "x" else fy))
                for z, _name, fx, fy in self.measure_beam_sizes(kind=kind)
                if z is not None and (fx if dim == "x" else fy) is not None
            ]

        z, fwhm = zip(*measurements)
        return GaussianBeam.from_fwhm_measurements(z, fwhm, wavelength)

    def plot_beam_sizes(
        self,
        beams=None,
        measurements=None,
        energy_eV=None,
        ax=None,
        z_range=None,
        show_layout=True,
        figsize=(12, 5),
    ):
        """Plot fitted (or given) beam-size envelopes in x/y together with
        the component layout, generalising the notebook applet's plot.

        `beams`: optional {"x": GaussianBeam, "y": GaussianBeam}; if not
        given, they are fit on the fly via `fit_beam_size`.
        `measurements`: optional {"x": [(z, fwhm), ...], "y": [...]} passed
        through to `fit_beam_size` (else live profile monitors are polled).
        """
        import matplotlib.pyplot as plt

        if beams is None:
            beams = {}
            meas_by_dim = measurements or {}
            for dim in ("x", "y"):
                try:
                    beams[dim] = self.fit_beam_size(
                        measurements=meas_by_dim.get(dim), dim=dim, energy_eV=energy_eV
                    )
                except ValueError as e:
                    print(f"Skipping {dim}: {e}")

        rows = [r for r in self.position_table(ref="sample") if r[2] is not None and r[3] is None]
        zs = [r[2] for r in rows]
        if z_range is None:
            if zs:
                pad = 0.15 * (max(zs) - min(zs) + 1)
                z_range = (min(zs) - pad, max(zs) + pad)
            else:
                z_range = (-1000, 1000)
        z_plot = np.linspace(z_range[0], z_range[1], 2000)

        if ax is None:
            _, ax = plt.subplots(figsize=figsize)

        colors = {"x": "#1f77b4", "y": "#d62728"}
        for dim, beam in beams.items():
            ax.plot(
                z_plot,
                beam.fwhm(z_plot) * 1e3,
                color=colors.get(dim, "k"),
                lw=2,
                label=f"{dim.upper()} FWHM (w0={beam.w0 * 1e3:.2f} um, z0={beam.z0:.1f} mm)",
            )
            if measurements and measurements.get(dim):
                mz, mf = zip(*measurements[dim])
                ax.scatter(mz, mf, color=colors.get(dim, "k"), zorder=5, marker="o")

        if show_layout:
            y_top = ax.get_ylim()[1]
            for name, kind, z, _z_end, _section, _desc in rows:
                color = self.KIND_COLORS.get(kind, "#999999")
                ax.axvline(z, color=color, lw=1, alpha=0.4)
                ax.text(z, y_top, name, rotation=90, va="top", ha="right", fontsize=7, color=color)

        ax.set_xlabel("z_sample [mm]")
        ax.set_ylabel("Beam size FWHM [um]")
        ax.grid(True, alpha=0.2)
        ax.legend(fontsize="small")
        return ax

    # ---- vertical text diagram (the __repr__) ----------------------------

    def _read_open_state(self, component):
        """Best-effort "is this blocking-type component open (beam passes)"
        across the various shapes seen in the codebase: this module's own
        `is_open`/`is_in_beam` Detectors, `PhotonShutter.request`,
        `Pulsepick.get_status()`, `BerninaVacuum.all_valves_open`, a raw
        `SafetyShutter.pv`, or a `VacuumSection.valve`. Returns `None` if it
        can't be determined (no live connection, unrecognised shape, ...) --
        never raises.

        `is_open` and `is_in_beam` are *opposite* senses of the same "does the
        beam get through" question -- `is_open=True` means beam passes, but
        `is_in_beam=True` means the device (a stopper, an inserted profile
        screen, ...) is sitting in the beam *blocking* it, i.e. NOT open. Each
        is inverted accordingly on the way to a single "open" boolean, so
        callers (this method's other users, `diagram()`'s beam-continuity
        cascade, the SVG panel's green/red glyph colouring) never have to know
        which convention a given component happens to use.
        """
        if component is None or isinstance(component, DummyComponent):
            return None
        for attr, invert in (("is_open", False), ("is_in_beam", True)):
            sub = getattr(component, attr, None)
            if sub is not None and hasattr(sub, "get_current_value"):
                try:
                    value = bool(sub.get_current_value())
                    return (not value) if invert else value
                except Exception:
                    return None
        valve = getattr(component, "valve", None)  # VacuumSection
        if valve is not None:
            return self._read_open_state(valve)
        request = getattr(component, "request", None)  # PhotonShutter
        if request is not None and hasattr(request, "get_current_value"):
            try:
                return bool(request.get_current_value())
            except Exception:
                return None
        get_status = getattr(component, "get_status", None)  # Pulsepick
        if callable(get_status):
            try:
                return get_status() == "open"
            except Exception:
                return None
        all_valves_open = getattr(component, "all_valves_open", None)  # BerninaVacuum
        if all_valves_open is not None and hasattr(all_valves_open, "get_current_value"):
            try:
                return bool(all_valves_open.get_current_value())
            except Exception:
                return None
        pv = getattr(component, "pv", None)  # SafetyShutter (raw epics.PV, no Detector wrapper)
        if pv is not None and hasattr(pv, "get"):
            try:
                value = pv.get()
                return None if value is None else bool(value)
            except Exception:
                return None
        return None

    def _read_gauge_ok_state(self, component):
        """Best-effort "is this gauge's reading in range" -- True if its
        pressure's live EPICS alarm severity is NO_ALARM, False if MINOR/MAJOR
        (a configured HIHI/LOLO/HIGH/LOW limit is tripped), None if the
        severity can't be read at all (INVALID, disconnected, or this isn't a
        `VacuumGauge`-shaped component) or `component` is missing/failed.
        Uses whatever range is *already configured on the PV itself* -- eco
        doesn't invent or duplicate a separate threshold.
        """
        if component is None or isinstance(component, DummyComponent):
            return None
        pressure = getattr(component, "pressure", None)
        get_severity = getattr(pressure, "get_severity", None)
        if not callable(get_severity):
            return None
        try:
            severity = get_severity()
        except Exception:
            return None
        if severity == 0:
            return True
        if severity in (1, 2):
            return False
        return None  # 3 (INVALID) or anything unexpected -> unknown

    def diagram(self, ref="source"):
        """A vertical, unicode schematic of the beamline: components listed
        top (upstream) to bottom (downstream) in `ref` order, connected by a
        beam line that switches from solid ("│") to dashed ("┆")
        the first time a `BLOCKING_KINDS` component reads as closed --
        everything further downstream has no beam. This is the vertical
        counterpart to the horizontal synoptic in `xoptics/Aramis.ui`.

        Each row is z / beam-line connector / kind glyph (always the kind's
        own `KIND_GLYPHS` symbol, e.g. a shutter is always "⬒" -- state is
        conveyed by colour, never by swapping in a different glyph) -- glyph
        and connector coloured together whenever a live state is available
        for that component, uncoloured (with an explanatory note appended to
        the component label) otherwise: green for open/clear, red at the row
        that is itself the closed `BLOCKING_KINDS` element (the connector
        switches to dashed only from the *next* row on -- this row still had
        beam), `IN_BEAM_COLOR` (orange) at a non-`BLOCKING_KINDS` component
        currently read as "in" the beam (`is_in_beam=True`, module-docstring
        in/out convention -- occludes locally but doesn't kill `beam_present`),
        plain red again for a `kind="gauge"` out-of-range reading (a fault,
        not an in/out state) -- / a best-effort status value (`Assembly.
        get_display_str`'s "status" column, i.e. `component.get_current_value()`
        if it has one) / an adjustable-detector-descend type marker (also
        lifted from `Assembly.get_display_str`: "✏️" adjustable, "👁️" detector,
        "↳" if the component is itself a sub-assembly with its own
        `status_collection`) / the component's name and kind.

        Rendered as a table via the same `format_table`/`rich` mechanism used
        for `get_status`/repr (`eco.utilities.tables`) rather than a hand-built
        string, with each beamline section (see `_iter_positions`) getting its
        own muted background colour (`eco.utilities.tables.section_row_styles`,
        palette `SECTION_BG_SHADES`) and alternating between that section's two
        shades row-by-row, so sections read as distinct blocks and individual
        rows stay easy to track by eye.

        Any not-yet-resolved `add_component(..., lazy=True)` component is
        resolved here (and, on failure, becomes a `DummyComponent` -- see
        `add_component(strict=False)`, the default), shown with a "?" and
        its error message instead of a state/glyph/status/type.

        A position registered with `parent=` (see `add_component`/
        `mark_position`) is unfolded directly under its parent's row instead
        of sitting wherever pure z order would put it -- see `_diagram_rows`
        -- and its name is indented ("↳") accordingly.
        """
        unit = "mm" if ref == "sample" else "m"
        rows = [r for r in self._diagram_rows(ref=ref) if r[2] is not None]
        zone_rows = [r for r in self.position_table(ref=ref) if r[1] == "zone"]
        if not rows and not zone_rows:
            return f"<empty Beamline '{self.name}'>"

        lines = [f"Beamline '{self.name}' ({len(rows)} components, z_{ref} [{unit}], downstream ↓)"]

        table_rows = []
        section_keys = []
        beam_present = True
        for name, kind, z, _z_end, section, description, depth in rows:
            connector = "│" if beam_present else "┆"
            component = self._find_component(name)
            note = ""
            glyph_color = None
            connector_color = None
            if isinstance(component, DummyComponent):
                glyph, note = "?", f"unavailable: {component.error}"
            else:
                # Always the kind's own glyph (see KIND_GLYPHS) -- open/closed
                # (or, for a gauge, in-range/alarm) is conveyed by colour, not
                # by swapping in a generic symbol, so e.g. a shutter always
                # reads as "⬒" and a valve always as "⋈" whether or not their
                # live state is known.
                glyph = self.KIND_GLYPHS.get(kind, "▪")
                state = (
                    self._read_gauge_ok_state(component)
                    if kind == "gauge"
                    else self._read_open_state(component)
                )
                if kind in self.BLOCKING_KINDS:
                    if state is True:
                        glyph_color = colorama.Fore.GREEN  # open
                    elif state is False:
                        # closed -- this is the blocking element itself, kills
                        # beam for every row after it (not this row -- that
                        # still had beam up to and including here).
                        glyph_color = colorama.Fore.RED
                        connector_color = colorama.Fore.RED
                        beam_present = False
                    else:
                        note = "state unknown (no live connection?)"
                elif kind == "gauge":
                    if state is True:
                        glyph_color = colorama.Fore.GREEN
                    elif state is False:
                        glyph_color = colorama.Fore.RED  # out-of-range reading, not an in/out state
                else:
                    if state is True:
                        glyph_color = colorama.Fore.GREEN  # clear, out of the beam
                    elif state is False:
                        # "in" the beam (see module-docstring is_in_beam
                        # convention) -- occludes locally but, unlike a
                        # BLOCKING_KINDS closure, doesn't kill beam_present.
                        glyph_color = self.IN_BEAM_COLOR
                        connector_color = self.IN_BEAM_COLOR
            if glyph_color:
                glyph = glyph_color + glyph + colorama.Style.RESET_ALL
            if connector_color:
                connector = connector_color + connector + colorama.Style.RESET_ALL

            status = "" if isinstance(component, DummyComponent) else self._component_value_str(component)
            typechar = self._type_char_str(component)

            indent = "  " * (depth - 1) + "↳ " if depth else ""
            label = f"{indent}{name} [{kind}]"
            if note:
                label += f"  ({note})"
            table_rows.append([f"{z:.2f}", connector, glyph, status, typechar, label])
            section_keys.append(section)

        row_styles = section_row_styles(section_keys, palette=self.SECTION_BG_SHADES)
        lines.append(format_table(
            table_rows, headers=["z", "", "", "status", "type", "component"],
            colalign=["right", "left", "left", "left", "left", "left"], row_styles=row_styles,
        ))

        if zone_rows:
            lines.append("-" * 44)
            for name, _kind, z, z_end, _section, description in zone_rows:
                component = self._find_component(name)
                try:
                    active = "ACTIVE" if bool(component.get_current_value()) else "inactive"
                except Exception:
                    active = "unknown"
                lines.append(f"zone  {name} [{z:.1f} .. {z_end:.1f}]: {active}")

        return "\n".join(lines)

    # ---- clickable SVG control panel -----------------------------------

    def _svg_items(self, ref="source", kinds=None, live=False):
        """Assemble the per-component dicts consumed by
        `eco.xoptics.beamline_svg.build_beamline_svg`: each carries the eco
        command path to run on click (`relpath`, resolved relative to this
        beamline so it works through `show()`'s namespace prefix), the visible
        `label`, `kind`, `z` (in `ref` frame), `section` and, if `live`, a
        `state` snapshot -- open/closed for blocking components, "clear"/"in
        beam" for profile/diagnostic (`is_in_beam`) devices, in-range/alarm
        (from the gauge's own EPICS severity, if available) for gauges, else
        None -- see `_read_open_state`/`_read_gauge_ok_state`."""
        items = []
        for section, pos in self._iter_positions():
            if kinds is not None and pos.kind not in kinds:
                continue
            if pos.kind == "zone":  # spans, not point devices -- skip on the panel
                continue
            z = pos.z_source if ref == "source" else pos.z_sample
            secname = section.name
            # path from this beamline to the device: "<section>.<name>" for a
            # joined beamline, just "<name>" when the section is this beamline
            # itself (single, un-joined section).
            if secname and secname != self.name:
                relpath = f"{secname}.{pos.name}"
            else:
                relpath = pos.name
            state = None
            if live and pos.kind in self.BLOCKING_KINDS:
                try:
                    state = self._read_open_state(self._find_component(pos.name))
                except Exception:
                    state = None
            elif live and pos.kind == "gauge":
                try:
                    state = self._read_gauge_ok_state(self._find_component(pos.name))
                except Exception:
                    state = None
            elif live and pos.kind in ("profile", "diagnostic"):
                # the other documented `is_in_beam` users (see add_is_in_beam
                # in this module) -- an inserted screen/diode blocking the
                # beam to measure it. Components that don't expose is_open/
                # is_in_beam at all just come back None (plain kind colour),
                # same as any other component _read_open_state can't place.
                try:
                    state = self._read_open_state(self._find_component(pos.name))
                except Exception:
                    state = None
            items.append(dict(
                relpath=relpath, label=pos.name, kind=pos.kind, z=z,
                section=secname, state=state,
            ))
        return items

    # `_svg()` is the dynamic-panel hook `Assembly.show()` looks for -- see
    # its docstring. Underscore-prefixed (not part of the public namespace a
    # user tab-completes into): a plain `.show()`/`.show(live=True)` already
    # opens the panel via this hook; `svg_panel()` below exists only because
    # it exposes `ref`/`kinds` filtering that generic `show()` doesn't know
    # about.
    def _svg(self, path=None, ref="source", kinds=None, live=False):
        """Build the clickable SVG control panel and write it to `path`
        (a temp file if None); return the file path. `kinds` filters which
        component kinds are drawn (e.g. `{"valve","gauge","pump"}` for a
        vacuum-only panel); `live=True` snapshots valve/shutter open state for
        colouring (touches EPICS)."""
        from .beamline_svg import build_beamline_svg
        from eco.utilities.tempfiles import user_temp_svg_path

        items = self._svg_items(ref=ref, kinds=kinds, live=live)
        svg_text = build_beamline_svg(items, title=self.name or "beamline", ref=ref)
        if path is None:
            path = user_temp_svg_path("eco_beamline", self.name or id(self))
        with open(path, "w") as f:
            f.write(svg_text)
        return path

    def svg_panel(self, in_window=False, ref="source", kinds=None, live=False,
                  exclude_group_ids=None):
        """Open the clickable SVG control panel of this beamline in the
        interactive viewer (Jupyter cell or, with `in_window=True`, a native Qt
        window -- picked automatically by `eco.utilities.svg_interactor`).
        Clicking a device symbol runs that component against this beamline in
        the live session. `kinds`/`live`/`ref` as in :meth:`_svg`; builds its
        own `_show_svg` up front (with those extra filters) so `Assembly.show`'s
        generic fallback -- which only knows `live=` -- doesn't rebuild it."""
        self._show_svg = self._svg(ref=ref, kinds=kinds, live=live)
        return self.show(in_window=in_window, exclude_group_ids=exclude_group_ids)

    def __repr__(self):
        return self.diagram()
