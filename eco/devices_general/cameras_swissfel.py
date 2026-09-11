from cam_server import CamClient, PipelineClient
from matplotlib.backend_bases import MouseButton
from eco.devices_general.utilities import Changer
from eco.epics_utils.detector import DetectorPvData, DetectorPvEnum

from ..aliases import Alias, append_object_to_object
from ..elements.adjustable import AdjustableVirtual, AdjustableGetSet, value_property
from eco.elements.detector import DetectorGet
from ..epics_utils.adjustable import AdjustablePv, AdjustablePvEnum
from eco.elements.adj_obj import AdjustableObject, DetectorObject
from .pipelines_swissfel import Pipeline
from ..elements.assembly import Assembly
from .motors import MotorRecord
import sys
from pathlib import Path
import time
import matplotlib.pyplot as plt
import numpy as np

sys.path.append("/sf/bernina/config/src/python/sf_databuffer/")
import bufferutils

CAM_CLIENT = None
PIPELINE_CLIENT = None


def get_camclient():
    global CAM_CLIENT
    if not CAM_CLIENT:
        CAM_CLIENT = CamClient()
        CAM_CLIENT.timeout = 8
    return CAM_CLIENT


def get_pipelineclient():
    global PIPELINE_CLIENT
    if not PIPELINE_CLIENT:
        PIPELINE_CLIENT = PipelineClient()
        PIPELINE_CLIENT.timeout = 8
    return PIPELINE_CLIENT


@value_property
class CamserverConfig2(Assembly):
    def __init__(self, cam_id, camserver_alias=None, name=None, camserver_group=None):
        super().__init__(name=name)
        self.cam_id = cam_id
        self.camserver_alias = camserver_alias
        self.camserver_group = camserver_group
        self._cross = None
        self._append(
            AdjustableGetSet,
            self._get_config,
            self._set_config,
            cache_get_seconds=0.05,
            precision=0,
            check_interval=None,
            name="_config",
            is_setting=True,
            is_display=False,
        )

        self._append(
            AdjustableObject,
            self._config,
            name="config",
            is_setting=False,
            is_display="recursive",
        )
        self._append(
            DetectorGet,
            self._get_info,
            cache_get_seconds=0.05,
            name="_info",
            is_setting=False,
            is_display=False,
        )
        self._append(
            DetectorObject,
            self._info,
            name="info",
            is_display="recursive",
            is_setting=False,
        )

    @property
    def pc(self):
        return get_pipelineclient()

    @property
    def cc(self):
        return get_camclient()

    def _get_config(self):
        return self.cc.get_camera_config(self.cam_id)

    def _set_config(self, value, hold=False):
        return Changer(
            target=value,
            changer=lambda v: self.cc.set_camera_config(self.cam_id, v),
            hold=hold,
        )

    def _get_info(self):
        fields = {
            "camera_geometry": self.cc.get_camera_geometry(self.cam_id),
            "pipelines": self._get_pipelines(),
        }
        return fields

    ### convenience functions ###
    def get_camera_image(self):
        im = self.cc.get_camera_array(self.cam_id)
        return im

    def set_alias(self, alias=None):
        """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
        if not alias:
            alias = self.camserver_alias
        self.set_config_fields({"alias": [alias.upper()]})

    def set_group(self, group=None):
        """adds the camera to the given group"""
        if not group:
            group = self.camserver_group
        self.config.group(group)

    def _get_pipelines(self):
        return [p for p in self.pc.get_pipelines() if self.cam_id in p]

    def set_config_fields(self, fields):
        """fields is a dictionary containing the keys and values that should be updated, e.g. fields={'group': ['Laser', 'Bernina']}"""
        config = self.cc.get_camera_config(self.cam_id)
        config.update(fields)
        self.cc.set_camera_config(self.cam_id, config)

    def set_config_fields_multiple_cams(self, conditions, fields):
        """
        conditions is a dictionary holding the conditions to select a subset of cameras, e.g. {"group": Bernina}
        fields is a dictionary containing the keys and values that should be updated, e.g. fields={'alias': ['huhu', 'duda']}
        """
        cams = {
            cam: self.cc.get_camera_config(cam)
            for cam in self.cc.get_cameras()
            if not "jungfrau" in cam
        }
        cams_selected = {}
        for cam, cfg in cams.items():
            try:
                if all([value in cfg[key] for key, value in conditions.items()]):
                    cfg.update(fields)
                    self.cc.set_camera_config(cam, cfg)
                    cams_selected[cam] = cfg
            except Exception as e:
                print(f"{type(e)} {e} in cam {cam}")
        return cams_selected

    def clear_all_bernina_aliases(self, verbose=True):
        cams_selected = self.set_config_fields_multiple_cams(
            conditions={"group": "Bernina"}, fields={"alias": []}
        )
        if verbose:
            print(f"Reset alias of {len(cams_selected)} cameras")
            print(cams_selected.keys())

    def _run_cmd(self, line, silent=True):
        if silent:
            print(f"Starting following commandline silently:\n" + line)
            with open(os.devnull, "w") as FNULL:
                subprocess.Popen(
                    line, shell=True, stdout=FNULL, stderr=subprocess.STDOUT
                )
        else:
            subprocess.Popen(line, shell=True)

    def gui(self):
        self._run_cmd(f"csm")


@value_property
class CamserverConfig(Assembly):
    def __init__(self, cam_id, camserver_alias=None, name=None, camserver_group=None):
        super().__init__(name=name)
        self.cam_id = cam_id
        self.camserver_alias = camserver_alias
        self.camserver_group = camserver_group

    @property
    def cc(self):
        return get_camclient()

    @property
    def pc(self):
        return get_pipelineclient()

    def get_current_value(self):
        return self.cc.get_camera_config(self.cam_id)

    def set_target_value(self, value, hold=False):
        return Changer(
            target=value,
            changer=lambda v: self.cc.set_camera_config(self.cam_id, v),
            hold=hold,
        )

    def set_config_fields(self, fields):
        """fields is a dictionary containing the keys and values that should be updated, e.g. fields={'group': ['Laser', 'Bernina']}"""
        config = self.get_current_value()
        config.update(fields)
        self.cc.set_camera_config(self.cam_id, config)

    ### convenience functions ###
    def set_alias(self, alias=None):
        """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
        if not alias:
            alias = self.camserver_alias
        self.set_config_fields({"alias": [alias.upper()]})

    def set_group(self, group=None):
        """creates an alias in the camera config on the server. If no alias is provided, it defaults to the camera name"""
        if not group:
            group = self.camserver_group
        self.set_config_fields({"group": group})

    def restart_pipeline(self):
        base_directory = "/sf/bernina/config/src/python/sf_databuffer/"
        label = self.cam_id

        policies = bufferutils.read_files(base_directory / Path("policies"), "policies")
        sources = bufferutils.read_files(base_directory / Path("sources"), "sources")
        sources_new = sources.copy()

        # Only for debugging purposes
        labeled_sources = bufferutils.get_labeled_sources(sources_new, label)
        for s in labeled_sources:
            bufferutils.logging.info(f"Restarting {s['stream']}")

        sources_new = bufferutils.remove_labeled_source(sources_new, label)

        # Stopping the removed source(s)
        bufferutils.update_sources_and_policies(sources_new, policies)

        # Starting the source(s) again
        bufferutils.update_sources_and_policies(sources, policies)

    def stop(self):
        self.cc.stop_instance(self.cam_id)

    def set_cross(self, x, y, x_um_per_px=None, y_um_per_px=None):
        """set x and y position of the refetence marker on a camera  px/um calibration is conserved if no new value is given"""
        calib = self.get_current_value()["camera_calibration"]
        if calib:
            if not x_um_per_px:
                x_um_per_px = calib["reference_marker_width"] / abs(
                    calib["reference_marker"][2] - calib["reference_marker"][0]
                )
            if not y_um_per_px:
                y_um_per_px = calib["reference_marker_height"] / abs(
                    calib["reference_marker"][3] - calib["reference_marker"][1]
                )
        else:
            calib = {}
            x_um_per_px = 1
            y_um_per_px = 1

        # calib["reference_marker"] = [x - 1, y - 1, x + 1, y + 1]
        calib["reference_marker_width"] = 2 * x_um_per_px
        calib["reference_marker_height"] = 2 * y_um_per_px
        self.set_config_fields(fields={"camera_calibration": calib})

    def set_config_fields_multiple_cams(self, conditions, fields):
        """
        conditions is a dictionary holding the conditions to select a subset of cameras, e.g. {"group": Bernina}
        fields is a dictionary containing the keys and values that should be updated, e.g. fields={'alias': ['huhu', 'duda']}
        """
        cams = {
            cam: self.cc.get_camera_config(cam)
            for cam in self.cc.get_cameras()
            if not "jungfrau" in cam
        }
        cams_selected = {}
        for cam, cfg in cams.items():
            try:
                if all([value in cfg[key] for key, value in conditions.items()]):
                    cfg.update(fields)
                    self.cc.set_camera_config(cam, cfg)
                    cams_selected[cam] = cfg
            except Exception as e:
                print(f"{type(e)} {e} in cam {cam}")
        return cams_selected

    def clear_all_bernina_aliases(self, verbose=True):
        cams_selected = self.set_config_fields_multiple_cams(
            conditions={"group": "Bernina"}, fields={"alias": []}
        )
        if verbose:
            print(f"Reset alias of {len(cams_selected)} cameras")
            print(cams_selected.keys())

    def __repr__(self):
        s = f"**Camera Server Config {self.cam_id} with Alias {self.name}**\n"
        for key, item in self.get_current_value().items():
            s += f"{key:20} : {item}\n"
        return s


def _spawn_separate_process_viewer(
    pvname, name=None, cam_class=None, pipeline_url=None, rate_hz=10.0, theme=None
):
    """Launch eco.widgets.camserver_stream_qt's own CLI as an independent
    OS process and embed its window into a local wrapper via
    eco.widgets.subprocess_embed -- the same spawn/WINID-handshake/embed
    mechanism eco.widgets.camserver_panel_qt already uses per-viewer (see
    that module and eco.widgets.subprocess_embed.spawn_and_embed), so the
    resulting window can be docked into the shared EcoDesktopApp workbench
    (see CameraBasler._default_dock_in) instead of popping untethered.

    Why a separate process at all: Qt's event loop is only pumped between
    IPython prompts (via the terminal's GUI-integration hook, same as
    eco.utilities.strip_plot's own module docstring explains for exactly
    this reason). A viewer built in-process therefore freezes -- both its
    live image updates and the whole window's responsiveness -- for the
    duration of any single blocking statement in this session (a
    synchronous motor move, a long scan, ...). A separate-process viewer
    has its own independent event loop untouched by that.

    `name`: the eco device's own alias name (e.g. "bernina.cam1", NOT the
    same as `pvname`) -- used for the window title and passed through as
    --eco-name. `cam_class`: a dotted import path (e.g.
    "eco.devices_general.cameras_swissfel.CameraBasler") the subprocess
    uses to build its OWN independent camera object via `cam_class(pvname)`,
    for the "Camera Settings"/Elog buttons and camera.screenpanel_ana
    access inside that window.

    DELIBERATE SIMPLIFICATION: the subprocess's camera object is a fresh,
    independent instance talking directly to EPICS/cam_server -- there is
    NO live IPC link back to this session's own camera object, so state
    set here (e.g. calibration tweaks) isn't reflected there and vice
    versa, beyond what both read from EPICS/cam_server directly. TODO: a
    real IPC channel (e.g. QLocalSocket/QLocalServer) to the parent
    process, for tighter live communication (shared analysis results,
    coordinated calibration state, ...), is a deliberately deferred future
    improvement -- not built here. For that tighter coupling right now,
    use `separate_process=False` (in-process, `cam=self` -- e.g.
    camera.screenpanel_ana for intensity computation) instead.

    Returns an eco.widgets.subprocess_embed.EmbeddedProcessWindow (the
    .window/.stop() wrapper convention every eco Qt widget follows -- see
    EcoDesktopApp._dock_widget_object) -- NOT a bare subprocess.Popen like
    before, since it must be dockable via Assembly.widget()'s dock_in
    machinery (see CameraBasler/CameraPCO._default_dock_in)."""
    import sys

    from eco.widgets.subprocess_embed import EmbeddedProcessWindow

    argv = [pvname, "--kind", "camera_pipeline", "--embed", "--rate", str(rate_hz)]
    if pipeline_url:
        argv += ["--pipeline-url", pipeline_url]
    if theme:
        argv += ["--theme", theme]
    if name:
        argv += ["--eco-name", name]
    if cam_class:
        argv += ["--cam-class", cam_class]

    cmd = [sys.executable, "-m", "eco.widgets.camserver_stream_qt", *argv]
    return EmbeddedProcessWindow(cmd, title=f"cam_server stream - {name or pvname}")


def get_camera_calibration(camera):
    """Read a camera's server-side "camera_calibration" config -- the same
    field pshell's own screen panel reads to draw its reticle correctly
    positioned and scaled -- as (center_x, center_y, x_um_per_px,
    y_um_per_px) in raw-frame pixel coordinates/units-per-pixel, or None if
    no calibration has ever been set. Works for both CameraBasler
    (CamserverConfig2) and CameraPCO (CamserverConfig) -- both expose the
    same .cc/.cam_id/set_config_fields() surface (see either class), read
    here directly rather than through CamserverConfig2's AdjustableObject-
    style `config.camera_calibration` wrapper so the same code works for
    both."""
    config = camera.config_cs.cc.get_camera_config(camera.config_cs.cam_id)
    calib = config.get("camera_calibration") or {}
    rm = calib.get("reference_marker")
    width = calib.get("reference_marker_width")
    height = calib.get("reference_marker_height")
    if not rm or not width or not height:
        return None
    center_x, center_y = (rm[0] + rm[2]) / 2, (rm[1] + rm[3]) / 2
    dx, dy = abs(rm[2] - rm[0]), abs(rm[3] - rm[1])
    x_um_per_px = width / dx if dx else None
    y_um_per_px = height / dy if dy else None
    return center_x, center_y, x_um_per_px, y_um_per_px


def set_camera_calibration(camera, x, y, x_um_per_px=None, y_um_per_px=None):
    """Write a camera's server-side "camera_calibration" config: position
    (x, y -- raw-frame pixel coordinates of the calibration reference
    point) and scale (x_um_per_px/y_um_per_px -- physical units per raw
    pixel, in whatever unit the caller is using consistently; despite the
    "_um_" in the name, pshell's own field doesn't care what physical unit
    it actually holds). Omitting x_um_per_px/y_um_per_px keeps the
    *existing* calibration's scale and only moves the position -- "set
    center position only" in CamServerStreamQt's Calibrate menu.

    Same encoding CameraBasler.set_cross() writes by hand via a blocking
    matplotlib click-to-pick figure: a 2x2-raw-pixel "reference_marker" box
    centered on (x, y), with reference_marker_width/height set to that
    box's *physical* size -- i.e. exactly 2*x_um_per_px/2*y_um_per_px, so
    the scale falls out again as width/2 (dividing by the box's own fixed
    2px size) on read (see get_camera_calibration). This is the shared,
    camera-class-agnostic write path CamServerStreamQt's calibration tools
    use -- see set_cross()'s docstring for why it's the intended
    successor.

    Returns the (x_um_per_px, y_um_per_px) actually written (resolved from
    the existing calibration when either was omitted)."""
    config = camera.config_cs.cc.get_camera_config(camera.config_cs.cam_id)
    calib = config.get("camera_calibration") or {}
    if x_um_per_px is None or y_um_per_px is None:
        existing = get_camera_calibration(camera)
        if x_um_per_px is None:
            x_um_per_px = existing[2] if existing and existing[2] else 1.0
        if y_um_per_px is None:
            y_um_per_px = existing[3] if existing and existing[3] else 1.0
    calib["reference_marker"] = [x - 1, y - 1, x + 1, y + 1]
    calib["reference_marker_width"] = 2 * x_um_per_px
    calib["reference_marker_height"] = 2 * y_um_per_px
    camera.config_cs.set_config_fields({"camera_calibration": calib})
    return x_um_per_px, y_um_per_px


def _camera_elog_post(
    camera,
    comment="",
    tags=None,
    n_average=1,
    animate=False,
    n_frames=8,
    fps=4.0,
    contrast_mode="auto",
    vmin=None,
    vmax=None,
    colormap="gray",
    log_scale=False,
):
    """Capture the current image (or, animate=True, a short GIF of
    n_frames consecutive frames) from `camera`'s own default processing
    pipeline and post it to the elog/scilog together with the acquisition/
    display settings that produced it. Shared by CameraBasler.elog()/
    CameraPCO.elog() -- see eco.widgets.camserver_stream_qt.
    capture_camera_snapshot, which does the actual headless capture (no
    viewer window needed), and CamServerStreamQt's own "Elog" toolbar
    button, which calls camera.elog() the same way so both paths produce
    identical images for the same settings."""
    from ..widgets.camserver_stream_qt import capture_camera_snapshot

    elog = camera._get_elog()
    if elog is None:
        raise RuntimeError(
            "no elog configured for this session (eco.defaults.ELOG is not set)"
        )

    path, stats = capture_camera_snapshot(
        camera.pvname,
        kind="camera_pipeline",
        n_average=n_average,
        animate=animate,
        n_frames=n_frames,
        fps=fps,
        contrast_mode=contrast_mode,
        vmin=vmin,
        vmax=vmax,
        colormap=colormap,
        log_scale=log_scale,
    )
    try:
        tname = camera.alias.get_full_name()
        lines = [f"### {tname} ({camera.pvname})"]
        if comment:
            lines.append(comment)
        for label, attr_name in (
            ("exposure_time (ms)", "exposure_time"),
            ("gain", "gain"),
            ("roi", "roi"),
        ):
            # CameraPCO has no "gain" (see its __init__) -- getattr/except
            # rather than hasattr-then-call so a live PV read glitch on one
            # setting can't take out the whole message
            adj = getattr(camera, attr_name, None)
            if adj is None:
                continue
            try:
                lines.append(f"{label}: {adj.get_current_value()}")
            except Exception:
                pass
        if n_average <= 1:
            avg_desc = "off"
        elif animate:
            avg_desc = f"{n_average} frames per GIF frame (running average)"
        else:
            avg_desc = f"{n_average} frames (running average)"
        lines.append(f"averaging: {avg_desc}")
        if stats.get("vmin") is not None:
            lines.append(
                f"color limits: [{stats['vmin']:.4g}, {stats['vmax']:.4g}] "
                f"(contrast_mode={contrast_mode}, colormap={colormap})"
            )
        message = "\n\n".join(lines)
        return elog.post(message, path, tags=tags or [])
    finally:
        path.unlink(missing_ok=True)


class CameraBasler(Assembly):
    # widget() (and anything driving it, e.g. the desktop app's namespace
    # launcher) opens the live cam_server viewer instead of the generic
    # property grid -- mirrors eco.devices_general.cameras_ptz.AxisPTZ. See
    # Assembly._default_widget/_widget_viewer() below.
    _default_widget = "_widget_viewer"
    # The viewer runs in a separate process by default (see
    # _widget_viewer's separate_process=True default), but stays standalone
    # unless the caller explicitly opts in with widget(dock_in=True) --
    # docking by default surprised users starting eco plainly (a viewer
    # they never asked to dock, docking anyway). dock_in=True still gets
    # the shared EcoDesktopApp workbench auto-created if none is open yet
    # (see Assembly._maybe_dock -> get_or_create_default_container()).
    _default_dock_in = None
    # Dotted import path the separate-process viewer subprocess uses to
    # rebuild its own independent camera object (see
    # _spawn_separate_process_viewer) -- kept as a class constant rather
    # than derived from type(self) so a subclass with a different __init__
    # signature (e.g. QioptiqMicroscope below) doesn't silently try to
    # reconstruct itself with the wrong arguments in the subprocess.
    _CAM_CLASS_PATH = "eco.devices_general.cameras_swissfel.CameraBasler"

    def __init__(
        self,
        pvname,
        camserver_alias=None,
        name=None,
        camserver_group=None,
        connect_camserver=True,
    ):
        super().__init__(name=name)
        self.pvname = pvname
        self._screenpanel_ana = None  # lazily built -- see .screenpanel_ana
        if not camserver_alias:
            camserver_alias = self.alias.get_full_name() + f" ({pvname})"
        else:
            camserver_alias = camserver_alias + f" ({pvname})"
        if connect_camserver:
            self._append(
                CamserverConfig2,
                self.pvname,
                camserver_alias=camserver_alias,
                camserver_group=camserver_group,
                name="config_cs",
                is_display=True,
                is_setting=True,
            )

            self.config_cs.set_alias()
            if camserver_group is not None:
                self.config_cs.set_group()
        self._append(
            AdjustablePvEnum,
            self.pvname + ":INIT",
            name="initialize",
            is_setting=True,
            is_display=False,
        )
        self._append(
            DetectorPvEnum,
            self.pvname + ":BUSY_INIT",
            name="is_initializing",
            is_setting=False,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":CAMERASTATUS",
            name="cam_status",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":BOARD",
            name="board_no",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":SERIALNR",
            name="serial_no",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":EXPOSURE",
            name="_exposure_time",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":ACQMODE",
            name="_acq_mode",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":RECMODE",
            name="_req_mode",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":STOREMODE",
            name="_store_mode",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":BINX",
            name="_binx",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":BINY",
            name="_biny",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONX_START",
            name="_roixmin",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONX_END",
            name="_roixmax",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONY_START",
            name="_roiymin",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":REGIONY_END",
            name="_roiymax",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SET_PARAM",
            name="_set_parameters",
            is_setting=True,
            is_display=False,
        )

        self._append(
            DetectorPvData,
            self.pvname + ":DEVICEFREQUENCY",
            has_unit=True,
            name="frequency",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":SW_PULSID_SRC",
            name="bscheck",
            is_setting=True,
            is_display=True,
        )
        self._append(
            DetectorPvData,
            self.pvname + ":ERRORCOUNTER",
            name="pulse_id_error_sum",
            is_setting=False,
            is_display=True,
        )
        self._append(
            DetectorPvData,
            self.pvname + ":FEEDBACKTIME0",
            name="response_time_bs",
            is_setting=False,
            is_display=True,
        )
        self._append(
            AdjustablePv,
            self.pvname + ":AMPGAIN",
            name="_gain",
            is_setting=True,
            is_display=False,
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":TRIGGER",
            name="trigger_on",
        )
        self._append(
            AdjustablePvEnum,
            self.pvname + ":TRIGGERSOURCE",
            name="trigger_source",
            is_setting=True,
            is_display=False,
        )
        # append_object_to_object(self,PvEnum,self.pvname+':TRIGGEREDGE',name='trigger_edge')
        self._append(
            AdjustableGetSet,
            self._exposure_time.get_current_value,
            lambda value: self._set_params((self._exposure_time, value)),
            name="exposure_time",
            unit="ms",
            is_setting=True,
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self._gain.get_current_value,
            lambda value: self._set_params((self._gain, value)),
            name="gain",
            is_setting=True,
            is_display=True,
        )

        def set_roi(roi):
            self._set_params(
                [self._roixmin, roi[0]],
                [self._roixmax, roi[1]],
                [self._roiymin, roi[2]],
                [self._roiymax, roi[3]],
            )
            return (roi[0], roi[1], roi[2], roi[3])

        self._append(
            AdjustableVirtual,
            [self._roixmin, self._roixmax, self._roiymin, self._roiymax],
            lambda x_from, x_to, y_from, y_to: [x_from, x_to, y_from, y_to],
            set_roi,
            name="roi",
            is_setting=True,
        )

    def _set_params(self, *args):
        self.cam_status(1)
        for ob, val in args:
            ob(val)
        self._set_parameters(1)
        self.cam_status(2)

    def re_initialize(self, wait_before_init=1, wait_for_init=3):
        self.cam_status(0)
        time.sleep(wait_before_init)
        self.cam_status(1)
        time.sleep(wait_for_init)
        self.cam_status(2)

    def get_camera_images(self, n):
        imgs = []
        while len(np.unique(imgs, axis=0)) < n:
            imgs.append(self.config_cs.get_camera_image())
            return np.unique(imgs, axis=0)

    def set_cross(
        self, x=None, y=None, x_um_per_px=None, y_um_per_px=None, n_images=10
    ):
        """set x and y position of the refetence marker on a camera  px/um calibration is conserved if no new value is given

        Superseded by CamServerStreamQt's own "Calibrate" menu (see
        viewer()/_widget_viewer() -- "Set center position only..." does
        exactly what this method does, minus the blocking matplotlib
        click-to-pick figure and terminal confirmation prompt; "2-line
        (ruler)..." additionally sets the scale interactively). Kept here
        for existing scripts; prefer the viewer for new use."""

        def prompt(x, y, x_um_per_px, y_um_per_px):
            x = int(x)
            y = int(y)
            answer = (
                input(
                    f"Set the new cross position [{x}, {y}] with calibration [{x_um_per_px:.3}, {y_um_per_px:.3}] ([y]/n)?"
                )
                or "y"
            )
            if answer == "y":
                calib.reference_marker([x - 1, y - 1, x + 1, y + 1])
                calib.reference_marker_width(2 * x_um_per_px)
                calib.reference_marker_height(2 * y_um_per_px)
                print("\nNew calibration:")
                print(calib)
            else:
                print("aborted")

        calib = self.config_cs.config.camera_calibration
        print("Current calibration:")
        print(calib)
        try:
            w = calib.reference_marker_width()
            h = calib.reference_marker_height()
            rm = calib.reference_marker()
            if not x_um_per_px:
                x_um_per_px = w / abs(rm[2] - rm[0])
            if not y_um_per_px:
                y_um_per_px = h / abs(rm[3] - rm[1])
        except:
            rm = [0, 0, 0, 0]
            x_um_per_px = 1
            y_um_per_px = 1
        if x is None or y is None:
            x = (rm[2] + rm[0]) / 2
            y = (rm[3] + rm[1]) / 2
            img = np.mean(self.get_camera_images(n_images), axis=0)
            run = True

            def on_click(event):
                if event.button is MouseButton.LEFT:
                    x = event.xdata
                    y = event.ydata
                    cross_plot.set_data(np.atleast_1d(x), np.atleast_1d(y))
                    plt.draw()
                    print(f"cross at x: {x:.4} and y: {y:.4}")
                    self.config_cs._cross = [x, y]
                else:
                    plt.disconnect(bid)
                    plt.close(self.config_cs.cam_id)

            fig = plt.figure(num=self.config_cs.cam_id)
            plt.title(f"Set cross: left mouse click, Finish: right click")
            plt.imshow(img)
            cross_plot = plt.plot(
                np.atleast_1d(x), np.atleast_1d(y), "+r", markersize=10
            )[0]
            bid = fig.canvas.mpl_connect("button_press_event", on_click)
            plt.show(block=True)
            x, y = self.config_cs._cross
            print(x, y)
        prompt(x, y, x_um_per_px, y_um_per_px)

    def gui(self):
        self._run_cmd(
            f'caqtdm -macro "NAME={self.pvname},CAMNAME={self.pvname}" /sf/controls/config/qt/Camera/CameraExpert.ui'
        )

    def _widget_viewer(
        self,
        pipeline_url=None,
        rate_hz=10.0,
        theme=None,
        auto_start=True,
        separate_process=True,
    ):
        """Open the live cam_server "screen panel" viewer for this camera's
        own default processing pipeline -- the pipeline name is resolved
        automatically from self.pvname (no need to know or guess a
        pipeline/instance name), mirroring pshell's own screen panel (see
        eco.widgets.camserver_stream_qt.resolve_camera_pipeline for the
        exact "{camera}_sp" naming convention and auto-create-if-missing
        behavior this reuses). The window's "Camera Settings" button opens
        widget(normal=True) (the normal property-grid display) -- mirrors
        eco.devices_general.cameras_ptz.AxisPTZ._widget_viewer().

        separate_process=True (the default): run the viewer in its own OS
        process instead of this one -- immune to this session blocking on
        something (a synchronous motor move, a long scan, ...) -- pops as
        its own standalone window by default, but can be docked into the
        shared EcoDesktopApp workbench instead via widget(dock_in=True)
        (see _default_dock_in/Assembly._maybe_dock), titled by this
        camera's own eco name, and with its own independently-built camera
        object (talking directly to EPICS/cam_server) behind its "Camera Settings" button
        -- see _spawn_separate_process_viewer for the full trade-off and
        the deferred-IPC TODO. Returns an
        eco.widgets.subprocess_embed.EmbeddedProcessWindow rather than a
        CamServerStreamQt instance in that case.

        separate_process=False: build the viewer in this process instead,
        with a direct (not rebuilt) `cam=self` reference -- e.g. for
        camera.screenpanel_ana intensity/analysis access tied to this
        exact object, at the cost of the viewer freezing for the duration
        of any blocking statement in this session."""
        alias = getattr(self, "alias", None)
        name = alias.get_full_name() if alias is not None else None

        if separate_process:
            return _spawn_separate_process_viewer(
                self.pvname,
                name=name,
                cam_class=getattr(self, "_CAM_CLASS_PATH", None),
                pipeline_url=pipeline_url,
                rate_hz=rate_hz,
                theme=theme,
            )

        from ..widgets.camserver_stream_qt import make_camserver_stream_qt

        return make_camserver_stream_qt(
            self.pvname,
            kind="camera_pipeline",
            pipeline_url=pipeline_url,
            rate_hz=rate_hz,
            theme=theme,
            auto_start=auto_start,
            cam=self,
            eco_name=name,
        )

    def elog(
        self,
        comment="",
        tags=None,
        n_average=1,
        animate=False,
        n_frames=8,
        fps=4.0,
        contrast_mode="auto",
        vmin=None,
        vmax=None,
        colormap="gray",
        log_scale=False,
    ):
        """Post the camera's current image to the elog/scilog, together
        with exposure_time/gain/roi and the averaging/color-limit settings
        used to produce it. Works standalone -- no viewer window needs to
        be open (see eco.widgets.camserver_stream_qt.capture_camera_
        snapshot, which does the actual capture); the live viewer's own
        "Elog" toolbar button calls this the same way, using whatever
        averaging/contrast/colormap it's currently showing.

        n_average>1: average that many frames first (a plain running
        average, same math as the viewer's own "Running" averaging mode --
        see FrameProcessor.average_mode -- just computed once rather than
        continuously).
        animate=True: post a short animated GIF of n_frames consecutive
        (each possibly n_average-averaged) frames at fps, instead of one
        still image -- e.g. to show a fluctuating or misaligned beam
        changing over a few frames rather than a single snapshot.
        contrast_mode/vmin/vmax/colormap/log_scale: as in FrameProcessor --
        "auto" (per-frame min/max, the default) or "manual" (needs vmin/
        vmax) or "full" (the dtype's own range)."""
        return _camera_elog_post(
            self,
            comment=comment,
            tags=tags,
            n_average=n_average,
            animate=animate,
            n_frames=n_frames,
            fps=fps,
            contrast_mode=contrast_mode,
            vmin=vmin,
            vmax=vmax,
            colormap=colormap,
            log_scale=log_scale,
        )

    @property
    def screenpanel_ana(self):
        """screenpanel_ana.<field_name> -- a live view of one scalar field
        this camera's own pipeline publishes alongside "image" (center of
        mass, intensity, gaussian-fit parameters, ...), e.g.
        camera.screenpanel_ana.intensity.get_current_value() or
        eco.utilities.strip_plot.strip_plot(camera.screenpanel_ana.
        intensity). See eco.widgets.camserver_stream_qt.ScreenpanelAnalysis
        for the full docstring, in particular the CAVEAT that this only
        reads whatever the pipeline already publishes -- a plain,
        non-analysis pipeline (cam_server's own default, what a camera
        gets until its pipeline is configured otherwise) publishes nothing
        beyond "image", so field access will simply time out.

        Built lazily (one background stream subscriber per camera, shared
        across every field read from it -- not one per field, and not
        reconnected on every read) on first access."""
        if self._screenpanel_ana is None:
            from ..widgets.camserver_stream_qt import ScreenpanelAnalysis

            self._screenpanel_ana = ScreenpanelAnalysis(self.pvname)
        return self._screenpanel_ana


# NB: please note this should be moved to microscopes which are using cameras plus zooms,
class QioptiqMicroscope(CameraBasler):
    def __init__(self, pvname_camera, pvname_zoom=None, pvname_focus=None, name=None):
        super().__init__(pvname_camera, name=name)
        if pvname_zoom:
            self._append(MotorRecord, pvname_zoom, name="zoom", is_setting=True)
        if pvname_focus:
            self._append(MotorRecord, pvname_focus, name="focus", is_setting=True)

    def _widget_viewer(self, **kwargs):
        """A short example of building a custom device widget purely by
        composing eco's existing pieces, no new Qt code of its own: the
        plain camera screen-panel viewer (CameraBasler._widget_viewer,
        via super()) stacked above a row of
        eco.widgets.indicator_widgets_qt_simple.slider() controls -- one
        per settable zoom/focus sub-Adjustable this microscope happens to
        have (see __init__). Each slider is built straight from the
        Adjustable itself (slider() reads/writes it directly via
        get_current_value()/set_target_value()) -- nothing here registers
        or configures anything beyond that."""
        from eco.widgets.containers import stack
        from eco.widgets.indicator_widgets_qt_simple import slider

        controls = [
            lambda name=name, label=label: slider(getattr(self, name), title=label)
            for name, label in (("zoom", "Zoom"), ("focus", "Focus"))
            if hasattr(self, name)
        ]
        viewer = super()._widget_viewer(**kwargs)
        if not controls:
            return viewer
        return stack(
            viewer, stack(*controls, direction="horizontal"), direction="vertical"
        )


class CameraPCO(Assembly):
    # widget() (and anything driving it, e.g. the desktop app's namespace
    # launcher) opens the live cam_server viewer instead of the generic
    # property grid -- mirrors CameraBasler/eco.devices_general.cameras_ptz.
    # AxisPTZ. See Assembly._default_widget/_widget_viewer() below.
    _default_widget = "_widget_viewer"
    # See CameraBasler's own copy of these two for the rationale --
    # mirrored here identically.
    _default_dock_in = None
    _CAM_CLASS_PATH = "eco.devices_general.cameras_swissfel.CameraPCO"

    def __init__(self, pvname, camserver_alias=None, name=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._screenpanel_ana = None  # lazily built -- see .screenpanel_ana
        if not camserver_alias:
            camserver_alias = self.alias.get_full_name() + f"({pvname})"
        else:
            camserver_alias = camserver_alias + f"({pvname})"
        self._append(
            CamserverConfig,
            self.pvname,
            camserver_alias=camserver_alias,
            name="config_cs",
        )
        self.config_cs.set_alias()
        self._append(AdjustablePvEnum, self.pvname + ":INIT", name="initialize")
        self._append(
            AdjustablePvEnum,
            self.pvname + ":CAMERASTATUS",
            name="camera_status",
            is_display=True,
        )
        self._append(AdjustablePv, self.pvname + ":BOARD", name="board_no")
        # self._append(AdjustablePv, self.pvname + ":SERIALNR", name="serial_no") Apparently not exisitng and timing out.
        self._append(AdjustablePv, self.pvname + ":EXPOSURE", name="_exposure_time")
        self._append(AdjustablePvEnum, self.pvname + ":ACQMODE", name="_acq_mode")
        self._append(AdjustablePvEnum, self.pvname + ":RECMODE", name="_req_mode")
        self._append(AdjustablePvEnum, self.pvname + ":STOREMODE", name="_store_mode")
        self._append(AdjustablePv, self.pvname + ":HSSPEED", name="_hs_speed")
        self._append(
            AdjustablePvEnum, self.pvname + ":SCMOSREADOUT", name="_readout_mode"
        )
        self._append(AdjustablePv, self.pvname + ":BINY", name="_binx")
        self._append(AdjustablePv, self.pvname + ":BINY", name="_biny")
        self._append(AdjustablePv, self.pvname + ":REGIONX_START", name="_roixmin")
        self._append(AdjustablePv, self.pvname + ":REGIONX_END", name="_roixmax")
        self._append(AdjustablePv, self.pvname + ":REGIONY_START", name="_roiymin")
        self._append(AdjustablePv, self.pvname + ":REGIONY_END", name="_roiymax")
        self._append(
            AdjustablePvEnum, self.pvname + ":SET_PARAM", name="_set_parameters"
        )
        self._append(AdjustablePvEnum, self.pvname + ":TRIGGER", name="trigger_on")
        # append_object_to_object(self,PvEnum,self.pvname+':TRIGGEREDGE',name='trigger_edge')

        self._append(
            AdjustableGetSet,
            self._exposure_time.get_current_value,
            lambda value: self._set_params((self._exposure_time, value)),
            name="exposure_time",
            is_setting=True,
        )
        self._append(
            AdjustableVirtual,
            [self.camera_status],
            lambda stat: stat == 2,
            lambda running: 2 if running else 1,
            name="running",
            is_setting=True,
        )

        def set_roi(roi):
            self._set_params(
                [self._roixmin, roi[0]],
                [self._roixmax, roi[1]],
                [self._roiymin, roi[2]],
                [self._roiymax, roi[3]],
            )
            return (roi[0], roi[1], roi[2], roi[3])

        self._append(
            AdjustableVirtual,
            [self._roixmin, self._roixmax, self._roiymin, self._roiymax],
            lambda x_from, x_to, y_from, y_to: [x_from, x_to, y_from, y_to],
            set_roi,
            name="roi",
            is_setting=True,
        )

    def _set_params(self, *args):
        self.running(False)
        for ob, val in args:
            ob(val)
        self._set_parameters(1)
        self.running(True)

    def gui(self):
        self._run_cmd(
            f'caqtdm -macro "NAME={self.pvname},CAMNAME={self.pvname}" /sf/controls/config/qt/Camera/CameraExpert.ui'
        )

    def _widget_viewer(
        self,
        pipeline_url=None,
        rate_hz=10.0,
        theme=None,
        auto_start=True,
        separate_process=True,
    ):
        """Open the live cam_server "screen panel" viewer for this camera's
        own default processing pipeline -- see CameraBasler._widget_viewer(),
        which this mirrors (separate_process included)."""
        alias = getattr(self, "alias", None)
        name = alias.get_full_name() if alias is not None else None

        if separate_process:
            return _spawn_separate_process_viewer(
                self.pvname,
                name=name,
                cam_class=getattr(self, "_CAM_CLASS_PATH", None),
                pipeline_url=pipeline_url,
                rate_hz=rate_hz,
                theme=theme,
            )

        from ..widgets.camserver_stream_qt import make_camserver_stream_qt

        return make_camserver_stream_qt(
            self.pvname,
            kind="camera_pipeline",
            pipeline_url=pipeline_url,
            rate_hz=rate_hz,
            theme=theme,
            auto_start=auto_start,
            cam=self,
            eco_name=name,
        )

    def elog(
        self,
        comment="",
        tags=None,
        n_average=1,
        animate=False,
        n_frames=8,
        fps=4.0,
        contrast_mode="auto",
        vmin=None,
        vmax=None,
        colormap="gray",
        log_scale=False,
    ):
        """Post the camera's current image to the elog/scilog -- see
        CameraBasler.elog(), which this mirrors (CameraPCO has no "gain"
        setting, so that line is simply omitted from the posted message;
        see _camera_elog_post)."""
        return _camera_elog_post(
            self,
            comment=comment,
            tags=tags,
            n_average=n_average,
            animate=animate,
            n_frames=n_frames,
            fps=fps,
            contrast_mode=contrast_mode,
            vmin=vmin,
            vmax=vmax,
            colormap=colormap,
            log_scale=log_scale,
        )

    @property
    def screenpanel_ana(self):
        """screenpanel_ana.<field_name> -- see CameraBasler.screenpanel_ana,
        which this mirrors."""
        if self._screenpanel_ana is None:
            from ..widgets.camserver_stream_qt import ScreenpanelAnalysis

            self._screenpanel_ana = ScreenpanelAnalysis(self.pvname)
        return self._screenpanel_ana


# NB: please note this should be moved to microscopes which are using cameras plus zooms,
class FeturaMicroscope(CameraBasler):
    def __init__(
        self, pvname_camera, pvname_base_zoom=None, name=None, camserver_alias=None
    ):
        super().__init__(pvname_camera, name=name, camserver_alias=camserver_alias)
        if pvname_base_zoom:
            self._append(
                AdjustablePv,
                pvsetname=pvname_base_zoom + ":POS_SP",
                pvreadbackname=pvname_base_zoom + ":POS_RB",
                name="_zoom_motor",
                is_setting=True,
                is_display=False,
            )

            def getv(v):
                return v / 10.0

            def setv(v):
                return v * 10.0

            self._append(
                AdjustableVirtual, [self._zoom_motor], getv, setv, name="zoom", unit="%"
            )
