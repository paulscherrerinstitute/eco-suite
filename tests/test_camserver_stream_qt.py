import time

import numpy as np
import pytest

pytest.importorskip("qtpy")

from eco.widgets.camserver_stream_qt import (
    _StreamWorker,
    normalize_to_uint8,
    resolve_stream,
)


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
