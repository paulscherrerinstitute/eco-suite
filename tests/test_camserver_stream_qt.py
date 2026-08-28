import time

import numpy as np
import pytest

pytest.importorskip("qtpy")
from qtpy import QtWidgets

from eco.widgets.camserver_stream_qt import (
    CamServerStreamQt,
    _StreamWorker,
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
