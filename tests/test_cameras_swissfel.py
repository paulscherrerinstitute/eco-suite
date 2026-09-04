"""Tests for CameraBasler/CameraPCO's ._widget_viewer() method and its
_default_widget = "_widget_viewer" wiring (eco.elements.assembly.Assembly.
widget()'s override mechanism -- see tests/test_assembly_default_widget.py
for that mechanism's own generic tests; these instead check that these two
camera classes plug into it correctly, resolving to the right cam_server
pipeline name for a real camera PV without the caller having to know/guess
one).

Importing eco.devices_general.cameras_swissfel itself requires `cam_server`
(a hard, module-level import there) -- pytest.importorskip below mirrors
tests/test_camserver_stream_qt.py's own convention for that.

CameraBasler/CameraPCO.__init__ makes real cam_server/EPICS calls (camserver
alias registration, PV connections), so these tests call ._widget_viewer()
as an unbound method against a bare stand-in object carrying just the
attributes it actually reads (pvname) rather than constructing a real
instance -- same spirit as test_assembly_default_widget.py's FakeCameraLike.
"""
import pytest

pytest.importorskip("cam_server")

from eco.devices_general.cameras_swissfel import (
    CameraBasler,
    CameraPCO,
    get_camera_calibration,
    set_camera_calibration,
)

# the two real PVs this feature was built/verified against
BASLER_PVNAME = "SARES20-PROF141-M1"  # eco.xdiagnostics.profile_monitors.ProfKbBernina's camera
PCO_PVNAME = "SARES20-PROF146-M1"  # eco.xdiagnostics.profile_monitors.Pprm_dsd's camera


class _FakeCameraSelf:
    def __init__(self, pvname):
        self.pvname = pvname


def test_camera_basler_default_widget_is_viewer():
    assert CameraBasler._default_widget == "_widget_viewer"


def test_camera_pco_default_widget_is_viewer():
    assert CameraPCO._default_widget == "_widget_viewer"


def test_camera_basler_viewer_resolves_its_own_camera_pipeline(monkeypatch):
    calls = {}

    def fake_make_camserver_stream_qt(name, **kwargs):
        calls["name"] = name
        calls["kwargs"] = kwargs
        return "the basler viewer"

    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        fake_make_camserver_stream_qt,
    )

    fake_self = _FakeCameraSelf(BASLER_PVNAME)
    result = CameraBasler._widget_viewer(fake_self)

    assert result == "the basler viewer"
    # the raw PV/camera name is passed through as-is, with kind=
    # "camera_pipeline" doing the "{camera}_sp" resolution (and
    # auto-creating that pipeline config if it doesn't exist yet) --
    # see eco.widgets.camserver_stream_qt.resolve_camera_pipeline
    assert calls["name"] == "SARES20-PROF141-M1"
    assert calls["kwargs"]["kind"] == "camera_pipeline"
    # cam=self: lets the viewer add its "Camera Settings" button (opens
    # widget(normal=True) on the real camera Assembly, not a copy of it)
    assert calls["kwargs"]["cam"] is fake_self


def test_camera_pco_viewer_resolves_its_own_camera_pipeline(monkeypatch):
    calls = {}

    def fake_make_camserver_stream_qt(name, **kwargs):
        calls["name"] = name
        calls["kwargs"] = kwargs
        return "the pco viewer"

    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        fake_make_camserver_stream_qt,
    )

    fake_self = _FakeCameraSelf(PCO_PVNAME)
    result = CameraPCO._widget_viewer(fake_self)

    assert result == "the pco viewer"
    assert calls["name"] == "SARES20-PROF146-M1"
    assert calls["kwargs"]["kind"] == "camera_pipeline"
    assert calls["kwargs"]["cam"] is fake_self


def test_camera_basler_viewer_passes_through_rate_and_theme(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        lambda name, **kwargs: calls.update(kwargs) or "viewer",
    )

    fake_self = _FakeCameraSelf(BASLER_PVNAME)
    CameraBasler._widget_viewer(fake_self, rate_hz=25.0, theme="dark", auto_start=False)

    assert calls["rate_hz"] == 25.0
    assert calls["theme"] == "dark"
    assert calls["auto_start"] is False


# -- separate_process=True: escapes this session's own event-loop freezes --


def test_camera_basler_viewer_separate_process_spawns_subprocess_instead(monkeypatch):
    calls = {}

    def fake_spawn(pvname, pipeline_url=None, rate_hz=10.0, theme=None):
        calls["pvname"] = pvname
        calls["pipeline_url"] = pipeline_url
        calls["rate_hz"] = rate_hz
        calls["theme"] = theme
        return "the subprocess handle"

    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel._spawn_separate_process_viewer", fake_spawn
    )
    # if separate_process=True took the in-process path instead, this would
    # blow up trying to build a real Qt window from a bare stand-in object
    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.make_camserver_stream_qt",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not build an in-process viewer")),
    )

    fake_self = _FakeCameraSelf(BASLER_PVNAME)
    result = CameraBasler._widget_viewer(fake_self, rate_hz=25.0, theme="dark", separate_process=True)

    assert result == "the subprocess handle"
    assert calls == {
        "pvname": BASLER_PVNAME, "pipeline_url": None, "rate_hz": 25.0, "theme": "dark",
    }


def test_camera_pco_viewer_separate_process_spawns_subprocess_instead(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel._spawn_separate_process_viewer",
        lambda pvname, **kwargs: calls.update(pvname=pvname, **kwargs) or "the subprocess handle",
    )

    fake_self = _FakeCameraSelf(PCO_PVNAME)
    result = CameraPCO._widget_viewer(fake_self, separate_process=True)

    assert result == "the subprocess handle"
    assert calls["pvname"] == PCO_PVNAME


def test_spawn_separate_process_viewer_builds_the_expected_command_line(monkeypatch):
    from eco.devices_general.cameras_swissfel import _spawn_separate_process_viewer

    captured = {}

    class _FakePopen:
        def __init__(self, argv, env=None):
            captured["argv"] = argv
            captured["env"] = env

    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    result = _spawn_separate_process_viewer(
        BASLER_PVNAME, pipeline_url="http://pipeline:8080", rate_hz=15.0, theme="dark"
    )

    assert isinstance(result, _FakePopen)
    argv = captured["argv"]
    assert argv[1:4] == ["-m", "eco.widgets.camserver_stream_qt", BASLER_PVNAME]
    assert "--kind" in argv and argv[argv.index("--kind") + 1] == "camera_pipeline"
    assert "--pipeline-url" in argv and argv[argv.index("--pipeline-url") + 1] == "http://pipeline:8080"
    assert "--rate" in argv and argv[argv.index("--rate") + 1] == "15.0"
    assert "--theme" in argv and argv[argv.index("--theme") + 1] == "dark"
    # the spawned process needs this checkout's eco on its own PYTHONPATH
    # (see _child_process_environment's own docstring in
    # eco.widgets.camserver_panel_qt for why -- a plain subprocess only
    # inherits environment variables, never this process's live sys.path)
    import os

    import eco

    eco_root = os.path.dirname(os.path.dirname(os.path.abspath(eco.__file__)))
    assert eco_root in captured["env"]["PYTHONPATH"].split(os.pathsep)


# -- get_camera_calibration / set_camera_calibration ----------------------
# The server-side "camera_calibration" config pshell's own screen panel
# reads for its reticle -- the shared write path CamServerStreamQt's
# Calibrate menu tools use (see get/set_camera_calibration's own
# docstrings), camera-class-agnostic (works the same for CameraBasler's
# CamserverConfig2 and CameraPCO's CamserverConfig -- both expose the same
# .cc/.cam_id/set_config_fields() surface).


class _FakeCamClient:
    def __init__(self, initial_config=None):
        self.cam_id = "my_camera"
        self.configs = {self.cam_id: dict(initial_config or {})}

    def get_camera_config(self, cam_id):
        return dict(self.configs[cam_id])

    def set_camera_config(self, cam_id, config):
        self.configs[cam_id] = dict(config)


class _FakeConfigCs:
    def __init__(self, cc):
        self.cc = cc
        self.cam_id = cc.cam_id

    def set_config_fields(self, fields):
        config = self.cc.get_camera_config(self.cam_id)
        config.update(fields)
        self.cc.set_camera_config(self.cam_id, config)


class _FakeCameraForCalibration:
    def __init__(self, initial_config=None):
        self.config_cs = _FakeConfigCs(_FakeCamClient(initial_config))


def test_get_camera_calibration_returns_none_when_unset():
    cam = _FakeCameraForCalibration()
    assert get_camera_calibration(cam) is None


def test_set_then_get_camera_calibration_round_trips():
    cam = _FakeCameraForCalibration()
    x_um, y_um = set_camera_calibration(cam, 100, 200, x_um_per_px=2.5, y_um_per_px=4.0)

    assert (x_um, y_um) == (2.5, 4.0)
    center_x, center_y, x_um_per_px, y_um_per_px = get_camera_calibration(cam)
    assert (center_x, center_y) == pytest.approx((100, 200))
    assert (x_um_per_px, y_um_per_px) == pytest.approx((2.5, 4.0))


def test_set_camera_calibration_position_only_keeps_existing_scale():
    """The "set center position only" case: omitting x_um_per_px/
    y_um_per_px must reuse the *existing* calibration's scale, not reset
    it to some default."""
    cam = _FakeCameraForCalibration()
    set_camera_calibration(cam, 100, 200, x_um_per_px=2.5, y_um_per_px=4.0)

    x_um, y_um = set_camera_calibration(cam, 300, 400)  # position only

    assert (x_um, y_um) == pytest.approx((2.5, 4.0))  # scale preserved
    center_x, center_y, x_um_per_px, y_um_per_px = get_camera_calibration(cam)
    assert (center_x, center_y) == pytest.approx((300, 400))  # position moved
    assert (x_um_per_px, y_um_per_px) == pytest.approx((2.5, 4.0))


def test_set_camera_calibration_with_no_prior_calibration_defaults_scale_to_one():
    cam = _FakeCameraForCalibration()
    x_um, y_um = set_camera_calibration(cam, 50, 60)  # no prior calibration at all
    assert (x_um, y_um) == (1.0, 1.0)


def test_set_camera_calibration_preserves_other_calibration_fields():
    """set_camera_calibration only touches reference_marker/width/height --
    any other keys already in camera_calibration must survive untouched."""
    cam = _FakeCameraForCalibration(
        initial_config={"camera_calibration": {"some_other_field": "keep me"}}
    )
    set_camera_calibration(cam, 10, 20, x_um_per_px=1.0, y_um_per_px=1.0)

    config = cam.config_cs.cc.get_camera_config(cam.config_cs.cam_id)
    assert config["camera_calibration"]["some_other_field"] == "keep me"


# -- .screenpanel_ana -----------------------------------------------------
# camera.screenpanel_ana.<field> -- a live Detector view of whatever
# analysis fields the camera's own pipeline happens to publish (see
# eco.widgets.camserver_stream_qt.ScreenpanelAnalysis for the real logic
# and its CAVEAT about pipeline-side configuration); here just checking
# the property lazily builds one and caches it on the camera instance.


def test_camera_basler_screenpanel_ana_lazily_builds_and_caches(monkeypatch):
    calls = []

    class FakeAnalysis:
        def __init__(self, camera_name):
            calls.append(camera_name)

    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.ScreenpanelAnalysis", FakeAnalysis
    )

    fake_self = _FakeCameraSelf(BASLER_PVNAME)
    fake_self._screenpanel_ana = None
    prop = CameraBasler.screenpanel_ana

    first = prop.fget(fake_self)
    second = prop.fget(fake_self)

    assert calls == [BASLER_PVNAME]  # constructed once
    assert first is second  # ... and reused, not rebuilt on every access
    assert isinstance(first, FakeAnalysis)


def test_camera_pco_screenpanel_ana_lazily_builds_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.ScreenpanelAnalysis",
        lambda camera_name: calls.append(camera_name) or object(),
    )

    fake_self = _FakeCameraSelf(PCO_PVNAME)
    fake_self._screenpanel_ana = None
    prop = CameraPCO.screenpanel_ana

    prop.fget(fake_self)
    prop.fget(fake_self)

    assert calls == [PCO_PVNAME]


# -- .elog() ------------------------------------------------------------
# CameraBasler/CameraPCO.elog() (via the shared _camera_elog_post) posts a
# freshly captured image plus acquisition/display settings to the elog --
# works standalone (no viewer window needed), and backs the live viewer's
# own "Elog" toolbar button. capture_camera_snapshot itself is mocked here
# (already covered directly, with a real fake bsread stream, in
# tests/test_camserver_stream_qt.py) -- these tests check what
# CameraBasler/CameraPCO.elog() do with its result: build the message,
# post it, and clean up the temp file.


class _FakeAdjustable:
    def __init__(self, value):
        self._value = value

    def get_current_value(self):
        return self._value


class _FakeAlias:
    def __init__(self, name):
        self._name = name

    def get_full_name(self):
        return self._name


class _FakeElog:
    def __init__(self):
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "mid-123"


class _FakeCameraForElog:
    """Stand-in carrying just what _camera_elog_post reads: .pvname,
    .alias, ._get_elog(), and optionally .exposure_time/.gain/.roi
    (CameraPCO has no .gain -- see cameras_swissfel.CameraPCO.__init__)."""

    def __init__(self, pvname, name="cam1", exposure_time=None, gain=None, roi=None):
        self.pvname = pvname
        self.alias = _FakeAlias(name)
        self._elog = _FakeElog()
        if exposure_time is not None:
            self.exposure_time = _FakeAdjustable(exposure_time)
        if gain is not None:
            self.gain = _FakeAdjustable(gain)
        if roi is not None:
            self.roi = _FakeAdjustable(roi)

    def _get_elog(self):
        return self._elog


def _mock_capture(monkeypatch, path, stats):
    captured = {}

    def fake_capture(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return path, stats

    monkeypatch.setattr(
        "eco.widgets.camserver_stream_qt.capture_camera_snapshot", fake_capture
    )
    return captured


def test_camera_basler_elog_posts_image_and_settings(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snap.png"
    snapshot_path.write_bytes(b"fake png bytes")
    captured = _mock_capture(monkeypatch, snapshot_path, {"vmin": 1.0, "vmax": 99.0, "shape": (4, 4)})

    fake_cam = _FakeCameraForElog(
        BASLER_PVNAME, exposure_time=12.5, gain=3, roi=(0, 100, 0, 100)
    )
    mid = CameraBasler.elog(fake_cam, comment="test comment", tags=["camera"], n_average=5)

    assert mid == "mid-123"
    assert captured["name"] == BASLER_PVNAME
    assert captured["kwargs"]["n_average"] == 5
    assert not snapshot_path.exists()  # cleaned up once posted

    (message, path_arg), kwargs = fake_cam._elog.calls[0]
    assert path_arg == snapshot_path
    assert kwargs["tags"] == ["camera"]
    assert "test comment" in message
    assert "exposure_time (ms): 12.5" in message
    assert "gain: 3" in message
    assert "roi: (0, 100, 0, 100)" in message
    assert "averaging: 5 frames" in message
    assert "color limits: [1, 99]" in message


def test_camera_pco_elog_omits_gain_line(monkeypatch, tmp_path):
    """CameraPCO has no "gain" setting -- confirms _camera_elog_post skips
    that line rather than erroring when the attribute is simply absent."""
    snapshot_path = tmp_path / "snap.png"
    snapshot_path.write_bytes(b"fake png bytes")
    _mock_capture(monkeypatch, snapshot_path, {"vmin": None, "vmax": None, "shape": (4, 4)})

    fake_cam = _FakeCameraForElog(PCO_PVNAME, exposure_time=8.0)
    CameraPCO.elog(fake_cam)

    (message, _path), _kwargs = fake_cam._elog.calls[0]
    assert "exposure_time (ms): 8.0" in message
    assert "gain" not in message
    assert "color limits" not in message  # vmin/vmax None -- e.g. a color image


def test_camera_elog_animate_averaging_description(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snap.gif"
    snapshot_path.write_bytes(b"fake gif bytes")
    _mock_capture(monkeypatch, snapshot_path, {"vmin": 0.0, "vmax": 10.0, "shape": (4, 4)})

    fake_cam = _FakeCameraForElog(BASLER_PVNAME)
    CameraBasler.elog(fake_cam, n_average=3, animate=True, n_frames=6)

    (message, _path), _kwargs = fake_cam._elog.calls[0]
    assert "3 frames per GIF frame" in message


def test_camera_elog_no_averaging_reports_off(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snap.png"
    snapshot_path.write_bytes(b"fake png bytes")
    _mock_capture(monkeypatch, snapshot_path, {"vmin": 0.0, "vmax": 10.0, "shape": (4, 4)})

    fake_cam = _FakeCameraForElog(BASLER_PVNAME)
    CameraBasler.elog(fake_cam)  # n_average defaults to 1

    (message, _path), _kwargs = fake_cam._elog.calls[0]
    assert "averaging: off" in message


def test_camera_elog_raises_when_no_elog_configured(monkeypatch):
    fake_cam = _FakeCameraForElog(BASLER_PVNAME)
    fake_cam._elog = None  # simulate eco.defaults.ELOG not set

    with pytest.raises(RuntimeError, match="no elog configured"):
        CameraBasler.elog(fake_cam)
