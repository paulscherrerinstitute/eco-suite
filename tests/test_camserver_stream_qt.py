import time

import numpy as np
import pytest

pytest.importorskip("qtpy")
from qtpy import QtCore, QtWidgets

from eco.widgets.camserver_stream_qt import (
    CamServerStreamQt,
    FrameProcessor,
    LivePipelineFields,
    ScreenpanelAnalysis,
    _HistogramColorbar,
    _StreamWorker,
    _build_cam_from_argv,
    _main,
    capture_camera_snapshot,
    default_pipeline_name,
    normalize_to_uint8,
    resolve_camera_pipeline,
    resolve_stream,
)

# real PV/cam_server names this feature was built against: SARES20-PROF141-M1
# is the CameraBasler behind eco.xdiagnostics.profile_monitors.ProfKbBernina,
# SARES20-PROF146-M1 the CameraPCO behind
# eco.xdiagnostics.profile_monitors.Pprm_dsd (see tests/test_cameras_swissfel.py
# for the ._widget_viewer()-level tests using these same two names)
BASLER_CAMERA_NAME = "SARES20-PROF141-M1"
PCO_CAMERA_NAME = "SARES20-PROF146-M1"


def test_normalize_to_uint8_full_range():
    arr = np.array([[0, 128], [255, 64]], dtype=np.uint16)
    out = normalize_to_uint8(arr)
    assert out.dtype == np.uint8
    assert out.min() == 0
    assert out.max() == 255


def test_normalize_to_uint8_fixed_range_clips():
    arr = np.array([[-10, 0], [50, 200]], dtype=np.float32)
    out = normalize_to_uint8(arr, vmin=0, vmax=100)
    assert out[0, 0] == 0  # clipped below vmin
    assert out[1, 1] == 255  # clipped above vmax
    assert out[1, 0] == pytest.approx(127, abs=2)


def test_normalize_to_uint8_flat_image_returns_zeros():
    arr = np.full((4, 4), 7, dtype=np.uint16)
    out = normalize_to_uint8(arr)
    assert np.all(out == 0)


def test_normalize_to_uint8_rejects_non_2d():
    with pytest.raises(ValueError):
        normalize_to_uint8(np.zeros((2, 2, 3)))


# -- FrameProcessor averaging: "running" (continuously-updated sliding
# window, the pre-existing default) vs "single" (accumulate N frames, emit
# their mean once, then start a fresh batch) -- mirrors pshell's own
# CamServerViewer.ImageIntegrator (decompiled from pshell-workbench: a
# negative "integration" count recomputes the mean over the last N frames
# on every new frame; non-negative accumulates N frames and emits their sum
# once every N frames).


def test_running_average_updates_every_frame():
    proc = FrameProcessor(average_n=3)
    assert proc.average_mode == "running"
    frames = [np.full((2, 2), v, dtype=np.float32) for v in (10, 20, 30, 40)]
    seen = [proc.process(f)[1]["values"][0, 0] for f in frames]
    # sliding window of size 3: 10, (10+20)/2, (10+20+30)/3, (20+30+40)/3
    assert seen == pytest.approx([10, 15, 20, 30])


def test_single_average_only_refreshes_every_n_frames():
    proc = FrameProcessor(average_n=3)
    proc.set_average_mode("single")
    frames = [np.full((2, 2), v, dtype=np.float32) for v in (10, 20, 30, 40, 50, 60, 70)]
    seen = [proc.process(f)[1]["values"][0, 0] for f in frames]
    # first batch (10,20,30) not complete until frame 3 -> raw frames shown
    # meanwhile; then the mean (20) holds through frames 4-5 (next batch
    # still filling); frame 6 completes batch 2 (40,50,60) -> mean 50 holds
    # through frame 7
    assert seen == pytest.approx([10, 20, 20, 20, 20, 50, 50])


def test_set_average_mode_rejects_unknown_value():
    proc = FrameProcessor()
    with pytest.raises(ValueError):
        proc.set_average_mode("bogus")


def test_set_average_mode_resets_in_progress_batch():
    proc = FrameProcessor(average_n=3)
    proc.set_average_mode("single")
    proc.process(np.full((2, 2), 10, dtype=np.float32))
    proc.process(np.full((2, 2), 20, dtype=np.float32))  # batch not yet complete
    proc.set_average_mode("running")
    proc.set_average_mode("single")  # back to single -- should start a clean batch
    frames = [np.full((2, 2), v, dtype=np.float32) for v in (1, 2, 3)]
    seen = [proc.process(f)[1]["values"][0, 0] for f in frames]
    assert seen == pytest.approx([1, 2, 2])  # mean(1,2,3)=2 lands on frame 3, not before


def test_default_pipeline_name_matches_pshell_convention():
    # ch.psi.pshell.screenpanel.CamServerViewer's default pipelineNameFormat
    # ("%s_sp") decompiled from pshell-workbench -- see
    # DEFAULT_PIPELINE_NAME_FORMAT's docstring
    assert default_pipeline_name(BASLER_CAMERA_NAME) == "SARES20-PROF141-M1_sp"
    assert default_pipeline_name(PCO_CAMERA_NAME) == "SARES20-PROF146-M1_sp"


def test_resolve_camera_pipeline_prefers_running_instance(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            assert name == "SARES20-PROF141-M1_sp"
            return "tcp://example.psi.ch:9999"

        def get_pipelines(self):
            raise AssertionError("should not need to look at pipeline configs")

        def create_instance_from_name(self, name):
            raise AssertionError("should not create a new instance")

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    pipeline_name, address = resolve_camera_pipeline(BASLER_CAMERA_NAME)
    assert pipeline_name == "SARES20-PROF141-M1_sp"
    assert address == "tcp://example.psi.ch:9999"


def test_resolve_camera_pipeline_creates_config_when_missing(monkeypatch):
    """The piece pshell's screen panel has that plain resolve_stream(kind=
    "pipeline") doesn't: not just reuse-or-create-an-instance-of-an-existing-
    config, but first auto-create the pipeline *config* itself (with just
    camera_name set) if even that doesn't exist yet."""
    cam_server = pytest.importorskip("cam_server")

    calls = []

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            raise RuntimeError("not running")

        def get_pipelines(self):
            return ["some_other_pipeline"]

        def save_pipeline_config(self, name, config):
            calls.append(("save", name, config))

        def create_instance_from_name(self, name):
            calls.append(("create", name))
            return ("generated-id", "tcp://example.psi.ch:8888")

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    pipeline_name, address = resolve_camera_pipeline(PCO_CAMERA_NAME)
    assert pipeline_name == "SARES20-PROF146-M1_sp"
    assert address == "tcp://example.psi.ch:8888"
    assert calls == [
        ("save", "SARES20-PROF146-M1_sp", {"camera_name": "SARES20-PROF146-M1"}),
        ("create", "SARES20-PROF146-M1_sp"),
    ]


def test_resolve_camera_pipeline_reuses_existing_config_without_resaving(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    calls = []

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            raise RuntimeError("not running")

        def get_pipelines(self):
            return ["SARES20-PROF141-M1_sp"]

        def save_pipeline_config(self, name, config):
            calls.append(("save", name, config))

        def create_instance_from_name(self, name):
            calls.append(("create", name))
            return ("generated-id", "tcp://example.psi.ch:8888")

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    resolve_camera_pipeline(BASLER_CAMERA_NAME)
    assert calls == [("create", "SARES20-PROF141-M1_sp")]  # no "save" call


def test_resolve_camera_pipeline_create_false_reraises(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            raise RuntimeError("not running")

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    with pytest.raises(RuntimeError):
        resolve_camera_pipeline(BASLER_CAMERA_NAME, create=False)


def test_resolve_stream_camera_pipeline_delegates(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            assert name == "SARES20-PROF146-M1_sp"
            return "tcp://example.psi.ch:7777"

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    address = resolve_stream(PCO_CAMERA_NAME, kind="camera_pipeline")
    assert address == "tcp://example.psi.ch:7777"


def test_resolve_stream_pipeline_prefers_running_instance(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakePipelineClient:
        def __init__(self, url=None):
            self.url = url

        def get_instance_stream(self, name):
            assert name == "my_pipeline"
            return "tcp://example.psi.ch:9999"

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    address = resolve_stream("my_pipeline", kind="pipeline")
    assert address == "tcp://example.psi.ch:9999"


def test_resolve_stream_pipeline_creates_when_not_running(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            raise RuntimeError("not running")

        def create_instance_from_name(self, name):
            return ("generated-id", "tcp://example.psi.ch:8888")

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    address = resolve_stream("my_pipeline", kind="pipeline")
    assert address == "tcp://example.psi.ch:8888"


def test_resolve_stream_pipeline_create_false_reraises(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            raise RuntimeError("not running")

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    with pytest.raises(RuntimeError):
        resolve_stream("my_pipeline", kind="pipeline", create=False)


def test_resolve_stream_camera_uses_camclient(monkeypatch):
    cam_server = pytest.importorskip("cam_server")

    class FakeCamClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            assert name == "my_camera"
            return "tcp://cam.psi.ch:1111"

    monkeypatch.setattr(cam_server, "CamClient", FakeCamClient)
    address = resolve_stream("my_camera", kind="camera")
    assert address == "tcp://cam.psi.ch:1111"


class _RecordingSignal:
    def __init__(self):
        self.calls = []

    def emit(self, value):
        self.calls.append(value)


class _RecordingBridge:
    def __init__(self):
        self.frame_ready = _RecordingSignal()
        self.error = _RecordingSignal()
        self.status = _RecordingSignal()


def test_stream_worker_emits_decoded_frames_and_stops_cleanly(monkeypatch):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")

    class FakeValue:
        def __init__(self, value):
            self.value = value

    class FakeCompactMessage:
        def __init__(self, pulse_id, image):
            self.pulse_id = pulse_id
            self.data = {"image": FakeValue(image)}

    class FakeMflowMessage:
        def __init__(self, data):
            self.data = data

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            return "tcp://fake-host:1234"

    class FakeSource:
        instances = []

        def __init__(self, host, port, mode, queue_size, receive_timeout):
            self.host, self.port = host, port
            self._n = 0
            FakeSource.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def receive(self):
            self._n += 1
            arr = np.full((2, 2), self._n % 256, dtype=np.uint16)
            return FakeMflowMessage(FakeCompactMessage(self._n, arr))

    FakeSource.instances = []
    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    monkeypatch.setattr(bsread, "Source", FakeSource)

    bridge = _RecordingBridge()
    worker = _StreamWorker("my_pipeline", "pipeline", bridge)
    worker.start()
    try:
        for _ in range(100):
            if len(bridge.frame_ready.calls) >= 3:
                break
            time.sleep(0.02)
    finally:
        worker.stop()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(bridge.frame_ready.calls) >= 3
    array, pulse_id = bridge.frame_ready.calls[0]
    assert array.shape == (2, 2)
    assert pulse_id == 1
    assert FakeSource.instances[0].host == "fake-host"
    assert FakeSource.instances[0].port == 1234
    assert bridge.status.calls == ["connected to tcp://fake-host:1234"]


def test_stream_worker_pause_stops_new_frames(monkeypatch):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")

    class FakeValue:
        def __init__(self, value):
            self.value = value

    class FakeCompactMessage:
        def __init__(self, pulse_id, image):
            self.pulse_id = pulse_id
            self.data = {"image": FakeValue(image)}

    class FakeMflowMessage:
        def __init__(self, data):
            self.data = data

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            return "tcp://fake-host:1234"

    class FakeSource:
        def __init__(self, host, port, mode, queue_size, receive_timeout):
            self._n = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def receive(self):
            self._n += 1
            arr = np.full((2, 2), self._n % 256, dtype=np.uint16)
            return FakeMflowMessage(FakeCompactMessage(self._n, arr))

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    monkeypatch.setattr(bsread, "Source", FakeSource)

    bridge = _RecordingBridge()
    worker = _StreamWorker("my_pipeline", "pipeline", bridge)
    worker.start()
    try:
        for _ in range(100):
            if len(bridge.frame_ready.calls) >= 2:
                break
            time.sleep(0.02)
        worker.pause(True)
        # give the in-flight receive()/emit() (if any) a moment to land,
        # then confirm no further frames arrive while paused
        time.sleep(0.1)
        count_at_pause = len(bridge.frame_ready.calls)
        time.sleep(0.3)
        assert len(bridge.frame_ready.calls) == count_at_pause
    finally:
        worker.stop()
        worker.join(timeout=2)


# -- LivePipelineFields / ScreenpanelAnalysis (headless, no Qt needed) ------


def _install_fake_analysis_stream(monkeypatch, cam_server, bsread, messages):
    """messages: a list of {field_name: raw_value} dicts, each yielded once
    (in order) by a fake bsread.Source, then further receive() calls
    return None (simulating an idle stream -- no more data, not a real
    error) -- mirrors the FakePipelineClient/FakeSource pattern used
    elsewhere in this file."""

    class FakeValue:
        def __init__(self, value):
            self.value = value

    class FakeCompactMessage:
        def __init__(self, pulse_id, fields):
            self.pulse_id = pulse_id
            self.data = {k: FakeValue(v) for k, v in fields.items()}

    class FakeMflowMessage:
        def __init__(self, data):
            self.data = data

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            return "tcp://fake-host:1234"

    class FakeSource:
        def __init__(self, host, port, mode, queue_size, receive_timeout):
            self._n = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def receive(self):
            if self._n >= len(messages):
                return None
            fields = messages[self._n]
            self._n += 1
            return FakeMflowMessage(FakeCompactMessage(self._n, fields))

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    monkeypatch.setattr(bsread, "Source", FakeSource)


def test_live_pipeline_fields_caches_scalar_fields_excluding_image(monkeypatch):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_analysis_stream(
        monkeypatch, cam_server, bsread,
        [
            {"image": np.zeros((2, 2)), "intensity": 100.0, "x_center_of_mass": 5.0},
            {"image": np.zeros((2, 2)), "intensity": 110.0, "x_center_of_mass": 5.2},
        ],
    )

    fields = LivePipelineFields("my_camera", kind="pipeline")
    fields.start()
    try:
        assert fields.wait_for_field("intensity", timeout=2.0) == pytest.approx(110.0)
        assert fields.wait_for_field("x_center_of_mass", timeout=2.0) == pytest.approx(5.2)
        assert set(fields.field_names()) == {"intensity", "x_center_of_mass"}
        assert "image" not in fields.field_names()
    finally:
        fields.stop()


def test_live_pipeline_fields_get_current_value_raises_for_unseen_field():
    fields = LivePipelineFields("my_camera", kind="pipeline")
    with pytest.raises(KeyError):
        fields.get_current_value("intensity")


def test_live_pipeline_fields_wait_for_field_times_out(monkeypatch):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_analysis_stream(monkeypatch, cam_server, bsread, [])

    fields = LivePipelineFields("my_camera", kind="pipeline")
    fields.start()
    try:
        with pytest.raises(TimeoutError):
            fields.wait_for_field("intensity", timeout=0.2)
    finally:
        fields.stop()


def test_screenpanel_analysis_field_access_returns_a_live_detector(monkeypatch):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_analysis_stream(
        monkeypatch, cam_server, bsread, [{"image": np.zeros((2, 2)), "intensity": 42.0}]
    )

    ana = ScreenpanelAnalysis("my_camera", kind="pipeline")
    try:
        det = ana.intensity
        assert det.get_current_value() == pytest.approx(42.0)  # Detector protocol
        assert det.name == "my_camera.intensity"
    finally:
        ana.stop()


def test_screenpanel_analysis_reuses_one_subscriber_across_fields(monkeypatch):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_analysis_stream(
        monkeypatch, cam_server, bsread,
        [{"image": np.zeros((2, 2)), "intensity": 1.0, "x_center_of_mass": 2.0}],
    )

    ana = ScreenpanelAnalysis("my_camera", kind="pipeline")
    try:
        # accessing two different fields must share the same background
        # subscriber/connection, not open one per field
        assert ana.intensity._fields is ana.x_center_of_mass._fields
    finally:
        ana.stop()


# -- capture_camera_snapshot (headless: no Qt window/QApplication needed) ---


def _install_fake_frames(monkeypatch, cam_server, bsread, frames):
    """Wire up a fake PipelineClient + bsread.Source that yields exactly
    `frames` (a list of 2D arrays), in order, then simulates a receive
    timeout -- mirrors the FakePipelineClient/FakeSource pattern used by
    the _StreamWorker tests above, factored out since
    capture_camera_snapshot's tests need several variations of it."""

    class FakeValue:
        def __init__(self, value):
            self.value = value

    class FakeCompactMessage:
        def __init__(self, pulse_id, image):
            self.pulse_id = pulse_id
            self.data = {"image": FakeValue(image)}

    class FakeMflowMessage:
        def __init__(self, data):
            self.data = data

    class FakePipelineClient:
        def __init__(self, url=None):
            pass

        def get_instance_stream(self, name):
            return "tcp://fake-host:1234"

    class FakeSource:
        def __init__(self, host, port, mode, queue_size, receive_timeout):
            self._n = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def receive(self):
            if self._n >= len(frames):
                return None  # simulates a real receive timeout
            arr = frames[self._n]
            self._n += 1
            return FakeMflowMessage(FakeCompactMessage(self._n, arr))

    monkeypatch.setattr(cam_server, "PipelineClient", FakePipelineClient)
    monkeypatch.setattr(bsread, "Source", FakeSource)


def _install_fake_stream(monkeypatch, cam_server, bsread, frame_values, shape=(4, 4)):
    """_install_fake_frames, but each entry in `frame_values` is a scalar
    -- yields one constant-valued frame per entry (convenient where the
    test just wants easy-to-check-the-mean-of values, not distinguishable
    image content)."""
    frames = [np.full(shape, v, dtype=np.float32) for v in frame_values]
    _install_fake_frames(monkeypatch, cam_server, bsread, frames)


def test_capture_camera_snapshot_writes_a_png(monkeypatch, tmp_path):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_stream(monkeypatch, cam_server, bsread, [42])

    path, stats = capture_camera_snapshot("my_pipeline", kind="pipeline", out_dir=tmp_path)

    assert path.exists()
    assert path.parent == tmp_path
    assert path.suffix == ".png"
    assert stats["shape"] == (4, 4)


def test_capture_camera_snapshot_averages_n_raw_frames_per_output(monkeypatch, tmp_path):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_stream(monkeypatch, cam_server, bsread, [10, 20, 30])

    path, stats = capture_camera_snapshot(
        "my_pipeline", kind="pipeline", n_average=3, out_dir=tmp_path
    )

    assert stats["values"][0, 0] == pytest.approx(20.0)  # mean(10, 20, 30)
    path.unlink()


def test_capture_camera_snapshot_animate_writes_a_multi_frame_gif(monkeypatch, tmp_path):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    from PIL import Image

    # distinguishable (non-flat) content per frame, and a fixed manual
    # contrast range: a *constant* array would hit normalize_to_uint8's
    # flat-image special case (-> all zeros) making every frame identical;
    # "auto" contrast (each frame normalized to its own min/max) would
    # even make an offset ramp like this look identical after normalizing
    # (linear rescale is offset/scale-invariant for a plain ramp) -- an
    # explicit manual range makes the raw offset differences actually show
    # up in the saved pixels, which is what this test wants to check
    frames = [np.arange(16).reshape(4, 4).astype(np.float32) + offset for offset in (0, 100, 200)]
    _install_fake_frames(monkeypatch, cam_server, bsread, frames)

    path, stats = capture_camera_snapshot(
        "my_pipeline", kind="pipeline", animate=True, n_frames=3, out_dir=tmp_path,
        contrast_mode="manual", vmin=0, vmax=300,
    )

    assert path.suffix == ".gif"
    with Image.open(path) as gif:
        frame_count = 0
        try:
            while True:
                gif.seek(frame_count)
                frame_count += 1
        except EOFError:
            pass
        assert frame_count == 3
    path.unlink()


def test_capture_camera_snapshot_raises_on_timeout(monkeypatch, tmp_path):
    cam_server = pytest.importorskip("cam_server")
    bsread = pytest.importorskip("bsread")
    _install_fake_stream(monkeypatch, cam_server, bsread, [])  # never yields a frame

    with pytest.raises(TimeoutError):
        capture_camera_snapshot("my_pipeline", kind="pipeline", out_dir=tmp_path, receive_timeout=0.01)


# -- "Camera Settings" button (cam=) -----------------------------------------
# kind="demo" throughout: exercises the cam=/_open_settings wiring with real
# Qt widgets but with no cam_server/network dependency at all (the demo
# source needs neither).


class _FakeCamAssembly:
    """Stand-in for a CameraBasler/CameraPCO: only .widget(normal=...)
    matters here -- see CameraBasler._widget_viewer()/CamServerStreamQt._open_settings."""

    def __init__(self):
        self.widget_calls = []

    def widget(self, normal=False):
        self.widget_calls.append(normal)
        return "the settings widget"


def _find_action(window, text):
    matches = [a for a in window.findChildren(QtWidgets.QAction) if a.text() == text]
    assert len(matches) == 1, f"expected exactly one {text!r} action, found {len(matches)}"
    return matches[0]


def test_camera_settings_button_present_when_cam_given():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_cam = _FakeCamAssembly()
    gui = CamServerStreamQt("demo", kind="demo", cam=fake_cam, auto_start=False)
    try:
        gui._build_window()
        action = _find_action(gui.window, "Camera Settings")
        action.trigger()
        # normal=True: opens the plain property grid, not this same viewer
        # again (CameraBasler/CameraPCO set _default_widget = "_widget_viewer") --
        # mirrors AxisPTZStreamQt._open_settings
        assert fake_cam.widget_calls == [True]
        assert gui._settings_window == "the settings widget"
    finally:
        gui.stop()


def test_camera_settings_button_absent_without_cam():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        matches = [a for a in gui.window.findChildren(QtWidgets.QAction) if a.text() == "Camera Settings"]
        assert matches == []
    finally:
        gui.stop()


# -- window title: eco device name vs. raw pvname/pipeline name --------


def test_window_title_uses_eco_name_when_given():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", eco_name="bernina.cam1", auto_start=False)
    try:
        gui._build_window()
        assert gui.window.windowTitle() == "cam_server stream - bernina.cam1"
    finally:
        gui.stop()


def test_window_title_falls_back_to_name_without_eco_name():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        assert gui.window.windowTitle() == "cam_server stream - demo"
    finally:
        gui.stop()


# -- --eco-name / --cam-class CLI plumbing (separate-process viewer) ---


def test_main_parses_eco_name_and_cam_class_flags(monkeypatch):
    captured = {}

    class _FakeViewer:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr("eco.widgets.camserver_stream_qt.CamServerStreamQt", _FakeViewer)
    # a nonexistent class path -- _build_cam_from_argv degrades to cam=None
    # rather than raising, so this doesn't need a real importable class
    _main(["demo", "--kind", "demo", "--eco-name", "bernina.cam1", "--cam-class", "not.a.real.Class"])

    assert captured["eco_name"] == "bernina.cam1"
    assert captured["cam"] is None
    assert captured["ran"] is True


def test_build_cam_from_argv_returns_none_and_logs_on_import_failure():
    assert _build_cam_from_argv("not.a.real.module.Class", "SOME-PV") is None


def test_build_cam_from_argv_constructs_the_named_class(monkeypatch):
    import sys
    import types

    calls = []

    class _FakeCam:
        def __init__(self, pvname):
            calls.append(pvname)

    fake_module = types.SimpleNamespace(FakeCam=_FakeCam)
    monkeypatch.setitem(sys.modules, "eco_test_fake_cam_module", fake_module)

    result = _build_cam_from_argv("eco_test_fake_cam_module.FakeCam", "PVNAME")

    assert isinstance(result, _FakeCam)
    assert calls == ["PVNAME"]


def test_build_cam_from_argv_falls_back_to_none_on_construction_failure(monkeypatch):
    import sys
    import types

    class _FakeCamBoom:
        def __init__(self, pvname):
            raise RuntimeError("EPICS unreachable")

    fake_module = types.SimpleNamespace(FakeCamBoom=_FakeCamBoom)
    monkeypatch.setitem(sys.modules, "eco_test_fake_cam_module_boom", fake_module)

    result = _build_cam_from_argv("eco_test_fake_cam_module_boom.FakeCamBoom", "PVNAME")

    assert result is None


# -- drag-to-zoom -------------------------------------------------------


def test_zoom_drag_toggle_is_mutually_exclusive_with_roi_and_marker():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._zoom_drag_btn.setChecked(True)
        assert gui._label.interaction_mode == "zoom"
        assert not gui._roi_btn.isChecked()
        assert not gui._marker_btn.isChecked()

        gui._roi_btn.setChecked(True)
        assert gui._label.interaction_mode == "roi"
        assert not gui._zoom_drag_btn.isChecked()

        gui._zoom_drag_btn.setChecked(True)
        gui._marker_btn.setChecked(True)
        assert gui._label.interaction_mode == "marker"
        assert not gui._zoom_drag_btn.isChecked()
    finally:
        gui.stop()


def test_zoom_dragged_sets_custom_scale_and_pending_center():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._zoom_drag_btn.setChecked(True)

        gui._on_zoom_dragged(10, 10, 110, 60)  # a 100x50 rect

        assert gui._zoom_mode not in ("fit",)
        assert isinstance(gui._zoom_mode, float)
        assert not any(a.isChecked() for a in gui._zoom_buttons)
        assert not gui._zoom_drag_btn.isChecked()
        assert gui._pending_zoom_center == (60.0, 35.0)  # center of the dragged rect
    finally:
        gui.stop()


def test_zoom_dragged_ignores_accidental_click():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._on_zoom_dragged(10, 10, 11, 10)  # 1x0 -- not a real drag

        assert gui._zoom_mode == "fit"
        assert gui._pending_zoom_center is None
    finally:
        gui.stop()


def test_apply_pending_zoom_center_clears_itself():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui.window.show()
        gui._pending_zoom_center = (42.0, 17.0)
        gui._apply_pending_zoom_center(1.0)
        assert gui._pending_zoom_center is None
    finally:
        gui.stop()


# -- settings dock fold/unfold restores window width ---------------------


def test_hiding_settings_dock_shrinks_window_and_showing_restores_it():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui.window.show()
        width_with_settings = gui.window.width()

        gui._settings_action.setChecked(False)
        assert gui._settings_dock.isVisible() is False
        assert gui.window.width() < width_with_settings
        assert gui._window_width_before_settings_hidden == width_with_settings

        gui._settings_action.setChecked(True)
        assert gui._settings_dock.isVisible() is True
        assert gui.window.width() == width_with_settings
        assert gui._window_width_before_settings_hidden is None
    finally:
        gui.stop()


# -- histogram/colorbar drag-a-region to set both color limits at once ---


class _FakeMouseEvent:
    def __init__(self, x, y):
        self._pos = QtCore.QPoint(x, y)

    def pos(self):
        return self._pos

    def globalPos(self):
        return self._pos


def _build_histogram():
    hist = _HistogramColorbar()
    hist.resize(90, 200)
    arr = np.linspace(0, 100, 100).reshape(10, 10)
    hist.set_data(arr, vmin=None, vmax=None, colormap="gray")
    return hist


def test_histogram_region_drag_sets_vmin_and_vmax():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hist = _build_histogram()
    received = []
    hist.levels_changed.connect(lambda vmin, vmax: received.append((vmin, vmax)))

    # y=80/y=140 are far from either handle (at y=0 and y=200 here -- see
    # class docstring/value_to_y), so this starts a region drag, not a
    # single-handle drag
    hist.mousePressEvent(_FakeMouseEvent(50, 80))
    assert hist._region_drag_start == 80
    hist.mouseMoveEvent(_FakeMouseEvent(50, 140))
    assert hist._region_drag_current == 140
    hist.mouseReleaseEvent(_FakeMouseEvent(50, 140))

    assert hist._region_drag_start is None
    assert hist._region_drag_current is None
    assert len(received) == 1
    vmin, vmax = received[0]
    assert vmin < vmax
    assert (hist._vmin, hist._vmax) == (vmin, vmax)


def test_histogram_region_drag_ignores_a_stray_click():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hist = _build_histogram()
    received = []
    hist.levels_changed.connect(lambda vmin, vmax: received.append((vmin, vmax)))
    original = (hist._vmin, hist._vmax)

    hist.mousePressEvent(_FakeMouseEvent(50, 80))
    hist.mouseReleaseEvent(_FakeMouseEvent(50, 81))  # 1px -- not a real drag

    assert received == []
    assert (hist._vmin, hist._vmax) == original


def test_histogram_single_handle_drag_still_works_unchanged():
    """Regression check: the pre-existing single-handle drag (press exactly
    on a handle) must still behave as before, unaffected by the new
    region-drag gesture living in the same mouse handlers."""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hist = _build_histogram()
    received = []
    hist.levels_changed.connect(lambda vmin, vmax: received.append((vmin, vmax)))

    max_handle_y = hist._y(hist._vmax)  # 0 for a freshly-built histogram
    hist.mousePressEvent(_FakeMouseEvent(50, max_handle_y))
    assert hist._dragging == "max"
    hist.mouseMoveEvent(_FakeMouseEvent(50, max_handle_y + 20))
    hist.mouseReleaseEvent(_FakeMouseEvent(50, max_handle_y + 20))

    assert hist._dragging is None
    assert received  # levels_changed fired live during the drag
    assert hist._vmax < 100.0  # moved down from the original data max


# -- Calibrate menu (numeric / 2-line / set-center-only) + Measure tool ----
# kind="demo" throughout (no cam_server/network needed for the viewer
# itself); calibration persistence is exercised against
# eco.devices_general.cameras_swissfel.get/set_camera_calibration mocked at
# that module level (where CamServerStreamQt's background-thread closures
# import them from) -- real end-to-end round-tripping through those
# functions is covered directly in tests/test_cameras_swissfel.py.


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_exclusive_tools_mutual_exclusion_holds_at_full_tool_count():
    """Regression check at the *current* tool count (6): this exact kind
    of cascade-ordering bug (an newly-checked tool's own mode getting
    stomped back to None by a sibling's forced uncheck) bit the 3-tool
    case once already -- see _on_tool_toggled's own docstring."""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._cal_unit = "um"  # skip calibrate_line's one-time unit-name prompt
        buttons = {
            "roi": gui._roi_btn,
            "zoom": gui._zoom_drag_btn,
            "marker": gui._marker_btn,
            "calibrate_line": gui._calibrate_line_btn,
            "calibrate_center": gui._calibrate_center_btn,
            "measure": gui._measure_btn,
        }
        for mode, button in buttons.items():
            button.setChecked(True)
            assert gui._label.interaction_mode == mode, mode
            for other_mode, other_button in buttons.items():
                if other_mode != mode:
                    assert not other_button.isChecked(), (mode, other_mode)
            button.setChecked(False)
            assert gui._label.interaction_mode is None
    finally:
        gui.stop()


def test_measure_tool_drag_sets_measurement_and_stays_active():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._measure_btn.setChecked(True)

        gui._on_line_dragged(10, 10, 110, 10)  # 100px horizontal line

        assert gui._measurement == (10, 10, 110, 10)
        assert gui._measure_btn.isChecked()  # stays active -- unlike ROI/zoom's one-shot tools
        assert gui._measurement_label() == "100 px"
    finally:
        gui.stop()


def test_measure_tool_ignores_accidental_click():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._measure_btn.setChecked(True)
        gui._on_line_dragged(10, 10, 11, 10)  # 1px -- not a real drag
        assert gui._measurement is None
    finally:
        gui.stop()


def test_measurement_label_uses_calibrated_units_and_per_axis_scale():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._cal_unit = "um"
        gui._cal_scale_x = 2.0
        gui._cal_scale_y = 3.0
        gui._measurement = (0, 0, 10, 0)  # 10px purely along X
        assert gui._measurement_label() == "20 um"
    finally:
        gui.stop()


def test_clear_measurement_action():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._measurement = (0, 0, 10, 0)
        gui._on_clear_measurement()
        assert gui._measurement is None
    finally:
        gui.stop()


def test_current_reticle_center_defaults_to_image_center_of_last_frame():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        assert gui._reticle_center is None
        gui._last_display_array = np.zeros((100, 200))
        assert gui._current_reticle_center() == (100.0, 50.0)
    finally:
        gui.stop()


def test_current_reticle_center_uses_explicit_value_when_set():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._reticle_center = (12.0, 34.0)
        gui._last_display_array = np.zeros((100, 200))  # must be ignored once explicit
        assert gui._current_reticle_center() == (12.0, 34.0)
    finally:
        gui.stop()


def test_calibrate_center_click_local_only_moves_reticle():
    """No cam= -- nothing to persist to, but the reticle still moves
    locally and the tool deactivates after one click (unlike Measure)."""
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._calibrate_center_btn.setChecked(True)
        gui._on_calibrate_center_clicked(123, 45)
        assert gui._reticle_center == (123, 45)
        assert not gui._calibrate_center_btn.isChecked()
    finally:
        gui.stop()


def test_calibrate_center_click_with_camera_persists_position_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel.set_camera_calibration",
        lambda cam, x, y, x_um=None, y_um=None: calls.append((cam, x, y, x_um, y_um)),
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_cam = object()
    gui = CamServerStreamQt("demo", kind="demo", cam=fake_cam, auto_start=False)
    try:
        gui._build_window()
        gui._on_calibrate_center_clicked(50, 60)

        assert _wait_until(lambda: len(calls) == 1)
        cam, x, y, x_um, y_um = calls[0]
        assert cam is fake_cam
        assert (x, y) == (50, 60)
        assert x_um is None and y_um is None  # position only -- existing scale kept server-side
        assert gui._reticle_center == (50, 60)
    finally:
        gui.stop()


def test_calibrate_line_tool_prompts_for_unit_name_on_first_use(monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("mm", True))
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        assert gui._cal_unit == "px"
        gui._calibrate_line_btn.setChecked(True)
        assert gui._cal_unit == "mm"
        assert gui._calibrate_line_btn.isChecked()  # tool actually starts
    finally:
        gui.stop()


def test_calibrate_line_tool_cancelled_unit_prompt_aborts_the_tool(monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._calibrate_line_btn.setChecked(True)
        assert not gui._calibrate_line_btn.isChecked()
        assert gui._cal_unit == "px"  # unchanged
    finally:
        gui.stop()


def test_calibrate_line_two_stage_flow_computes_per_axis_scale(monkeypatch):
    # X stage: 100 (unit) over a 50px line -> 2 unit/px; Y stage: 30 over 10px -> 3 unit/px
    responses = iter([(100.0, True), (30.0, True)])
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getDouble",
        staticmethod(lambda *a, **k: next(responses)),
    )
    calls = []
    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel.set_camera_calibration",
        lambda cam, x, y, x_um=None, y_um=None: calls.append((cam, x, y, x_um, y_um)),
    )

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_cam = object()
    gui = CamServerStreamQt("demo", kind="demo", cam=fake_cam, auto_start=False)
    try:
        gui._build_window()
        gui._cal_unit = "um"  # skip the unit-name prompt for this test
        gui._calibrate_line_btn.setChecked(True)

        gui._on_line_dragged(0, 0, 50, 0)  # X stage: 50px horizontal
        assert gui._calib_line_stage == "y"
        assert gui._calibrate_line_btn.isChecked()  # stays active for the 2nd line

        gui._on_line_dragged(0, 0, 0, 10)  # Y stage: 10px vertical

        assert not gui._calibrate_line_btn.isChecked()  # done
        assert gui._calib_line_stage is None
        assert _wait_until(lambda: len(calls) == 1)
        cam, x, y, x_um, y_um = calls[0]
        assert cam is fake_cam
        assert (x_um, y_um) == pytest.approx((2.0, 3.0))
    finally:
        gui.stop()


def test_calibrate_line_cancel_mid_stage_resets_state(monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getDouble", staticmethod(lambda *a, **k: (0.0, False))
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._cal_unit = "um"
        gui._calibrate_line_btn.setChecked(True)

        gui._on_line_dragged(0, 0, 50, 0)  # X stage, but the dialog is cancelled

        assert not gui._calibrate_line_btn.isChecked()
        assert gui._calib_line_stage is None
        assert gui._calib_line_x_um_per_px is None
    finally:
        gui.stop()


def test_switching_tool_mid_calibration_line_resets_staged_state(monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getDouble", staticmethod(lambda *a, **k: (100.0, True))
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)
    try:
        gui._build_window()
        gui._cal_unit = "um"
        gui._calibrate_line_btn.setChecked(True)
        gui._on_line_dragged(0, 0, 50, 0)  # completes the X stage
        assert gui._calib_line_stage == "y"

        gui._roi_btn.setChecked(True)  # switch away before finishing the Y line

        assert not gui._calibrate_line_btn.isChecked()
        assert gui._calib_line_stage is None
        assert gui._calib_line_x_um_per_px is None
        assert gui._label.interaction_mode == "roi"
    finally:
        gui.stop()


def test_calibrate_numeric_updates_unit_and_persists(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel.set_camera_calibration",
        lambda cam, x, y, x_um=None, y_um=None: calls.append((cam, x, y, x_um, y_um)),
    )

    class FakeDialog:
        def __init__(self, *a, **k):
            self.scale_x = 7.0
            self.scale_y = 8.0
            self.unit = "mm"

        def exec_(self):
            return QtWidgets.QDialog.Accepted

    monkeypatch.setattr("eco.widgets.camserver_stream_qt._CalibrationDialog", FakeDialog)

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_cam = object()
    gui = CamServerStreamQt("demo", kind="demo", cam=fake_cam, auto_start=False)
    try:
        gui._build_window()
        gui._on_calibrate_numeric()

        assert gui._cal_unit == "mm"
        assert _wait_until(lambda: len(calls) == 1)
        cam, x, y, x_um, y_um = calls[0]
        assert cam is fake_cam
        assert (x_um, y_um) == (7.0, 8.0)
    finally:
        gui.stop()


def test_load_camera_calibration_populates_state_on_open(monkeypatch):
    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel.get_camera_calibration",
        lambda cam: (55.0, 66.0, 2.5, 3.5),
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_cam = object()
    gui = CamServerStreamQt("demo", kind="demo", cam=fake_cam, auto_start=False)
    try:
        gui._build_window()  # calls _load_camera_calibration internally

        assert _wait_until(lambda: gui._reticle_center == (55.0, 66.0))
        assert gui._cal_scale_x == pytest.approx(2.5)
        assert gui._cal_scale_y == pytest.approx(3.5)
        assert gui._cal_unit == "um"  # promoted from the "px" default
    finally:
        gui.stop()


def test_load_camera_calibration_leaves_defaults_when_none_exists(monkeypatch):
    monkeypatch.setattr(
        "eco.devices_general.cameras_swissfel.get_camera_calibration", lambda cam: None
    )
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_cam = object()
    gui = CamServerStreamQt("demo", kind="demo", cam=fake_cam, auto_start=False)
    try:
        gui._build_window()
        time.sleep(0.2)  # give the background thread a moment to run and return early
        assert gui._reticle_center is None
        assert gui._cal_unit == "px"
    finally:
        gui.stop()


def test_load_camera_calibration_skipped_when_no_camera():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    gui = CamServerStreamQt("demo", kind="demo", auto_start=False)  # no cam=
    try:
        gui._build_window()
        assert gui._reticle_center is None
    finally:
        gui.stop()
