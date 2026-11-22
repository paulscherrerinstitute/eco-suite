"""eco.widgets.camera_frames: the camera viewer's frame processing (no Qt)
and its frame process."""

import os
import pickle
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from eco.widgets import camera_frames as cf


def gaussian_image(h=120, w=200, cx=130.0, cy=50.0, sx=12.0, sy=6.0, background=10.0, seed=0):
    yy, xx = np.mgrid[0:h, 0:w]
    image = background + 400 * np.exp(-((xx - cx) ** 2 / (2 * sx**2) + (yy - cy) ** 2 / (2 * sy**2)))
    return image + np.random.default_rng(seed).normal(0, 2, (h, w))


def test_the_module_imports_neither_qt_nor_eco():
    """The frame process starts as a plain script: a Qt or eco import there
    would cost seconds (eco: ~50 s cold on the consoles)."""
    code = (
        "import sys, runpy; sys.argv = ['x']; "
        f"runpy.run_path({cf.__file__!r}, run_name='not_main'); "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('eco', 'qtpy', 'PyQt5', 'PySide6')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_gauss_fit_matches_the_pipeline(monkeypatch):
    functions = pytest.importorskip("cam_server.pipeline.data_processing.functions")
    # cam_server's own code still calls np.trapz, which numpy 2.5 removed
    monkeypatch.setattr(np, "trapz", getattr(np, "trapezoid", None) or np.trapz, raising=False)
    profile = gaussian_image().sum(axis=0)
    axis = np.linspace(-300, 500, profile.size)  # a calibrated axis, as a pipeline sends
    ours = cf.gauss_fit(profile, axis)
    curve, offset, amplitude, mean, sigma, com, rms = functions.gauss_fit(profile, axis)
    assert ours["converged"]
    assert ours["mean"] == pytest.approx(mean, rel=1e-6)
    assert ours["sigma"] == pytest.approx(sigma, rel=1e-6)
    assert ours["amplitude"] == pytest.approx(amplitude, rel=1e-6)
    assert ours["com"] == pytest.approx(com) and ours["rms"] == pytest.approx(rms)
    np.testing.assert_allclose(ours["curve"], curve, rtol=1e-6)


def test_a_fit_centered_off_the_axis_is_marked_not_converged():
    profile = np.linspace(0, 100, 50)  # a ramp: the "peak" lies beyond the end
    fit = cf.gauss_fit(profile, np.arange(50.0))
    assert fit is not None and not fit["converged"]


def test_gauss_fit_of_a_flat_profile_is_none():
    assert cf.gauss_fit(np.zeros(50), np.arange(50)) is None


@pytest.mark.parametrize("seed", range(5))
def test_good_region_matches_the_pipeline(seed):
    functions = pytest.importorskip("cam_server.pipeline.data_processing.functions")
    rng = np.random.default_rng(seed)
    for _ in range(20):
        profile = gaussian_image(cx=rng.uniform(5, 195), sx=rng.uniform(2, 60), seed=seed).sum(axis=0)
        threshold, gfscale = rng.uniform(0.1, 0.6), rng.uniform(1.0, 3.0)
        assert cf.good_region_indices(profile, threshold, gfscale) == functions.get_good_region_profile(
            profile, threshold, gfscale
        )


def test_analyse_reports_raw_frame_pixels_and_the_good_region():
    image = gaussian_image()
    roi = image[20:100, 60:200]  # what the viewer shows with ROI (60, 20, 140, 80)
    result = cf.analyse(roi, fit=True, good_region=(0.3, 1.8), offset=(60, 20))
    assert result["x_fit"]["mean"] == pytest.approx(130, abs=0.5)
    assert result["y_fit"]["mean"] == pytest.approx(50, abs=0.5)
    assert result["x_fit"]["sigma"] == pytest.approx(12, abs=1)
    gr = result["good_region"]
    x0, y0, x1, y1 = gr["region"]
    assert x0 < 130 < x1 and y0 < 50 < y1
    assert gr["x_fit"]["mean"] == pytest.approx(130, abs=0.5)


def test_downsampled_display_keeps_the_full_resolution_contrast():
    image = gaussian_image().astype(np.uint16)
    processor = cf.FrameProcessor()
    full, stats = processor.process(image)
    small, stats_small = processor.process(image, downsample=4, new_frame=False)
    assert small.shape == (30, 50)
    assert stats_small["vmax"] == stats["vmax"] and stats_small["shape"] == stats["shape"]
    assert stats_small["values"].shape == image.shape
    # the block mean of the full-resolution display, within rounding
    np.testing.assert_allclose(small, cf.block_mean(full.astype(float), 4), atol=1.5)


def test_reprocessing_does_not_average_the_same_frame_twice():
    processor = cf.FrameProcessor(average_n=2)
    processor.process(np.zeros((4, 4)))
    processor.process(np.full((4, 4), 10.0))
    _, stats = processor.process(np.full((4, 4), 10.0), new_frame=False)
    assert stats["values"][0, 0] == pytest.approx(5.0)


class FrameProcessClient:
    """The viewer's end of the protocol, without Qt."""

    def __init__(self):
        import zmq

        self.zmq = zmq
        self.dir = cf.private_runtime_dir()
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.setsockopt(zmq.LINGER, 0)
        endpoint = f"ipc://{self.dir}/frames"
        self.socket.bind(endpoint)
        self.proc = subprocess.Popen([sys.executable, cf.__file__, endpoint, str(os.getpid())])
        self.receive("hello")
        shutil.rmtree(self.dir, ignore_errors=True)  # as the viewer does once connected

    def send(self, **message):
        self.socket.send(pickle.dumps(message))

    def receive(self, kind, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.socket.poll(100):
                parts = self.socket.recv_multipart()
                message = pickle.loads(parts[0])
                if message["type"] == kind:
                    return message, parts[1:]
        raise TimeoutError(kind)

    def close(self):
        self.send(type="stop")
        self.proc.wait(timeout=5)
        self.socket.close(0)
        self.context.term()


@pytest.fixture
def frame_process():
    pytest.importorskip("zmq")
    client = FrameProcessClient()
    yield client
    if client.proc.poll() is None:
        client.close()


def test_frame_process_sends_screen_sized_frames_with_fits(frame_process):
    client = frame_process
    t0 = time.monotonic()
    client.send(type="demo", rate=20.0)
    client.send(type="settings", downsample=2, fit=True, profile=True, good_region=(0.3, 1.8))
    client.send(type="probe", probes={"cursor": (10, 20)})
    message, (image,) = client.receive("frame")
    startup = time.monotonic() - t0
    assert startup < 5, startup
    while message["analysis"] is None or message["analysis"]["good_region"] is None:
        message, (image,) = client.receive("frame")
    assert message["display_shape"] == (150, 200)  # the 300x400 demo frame, halved
    assert len(image) == 150 * 200
    assert message["stats"]["shape"] == (300, 400)
    fit = message["analysis"]["x_fit"]
    # the demo blob's own center is sent along as a pipeline field
    assert fit["mean"] == pytest.approx(message["fields"]["x_center_of_mass"], abs=2)
    assert message["roi_stats"]["x_com"] == pytest.approx(fit["mean"], abs=3)
    assert "cursor" in message["probes"]
    center = (round(fit["mean"]), round(message["analysis"]["y_fit"]["mean"]))
    client.send(type="probe", probes={"cursor": center})
    probes, _ = client.receive("probes")
    assert probes["probes"]["cursor"] > 2000  # the blob's peak is 5000 (it moves a little)
    assert cf.FrameWorker.result_scalars(message)["gr_fit_y_mean"] == pytest.approx(
        message["fields"]["y_center_of_mass"], abs=2
    )


def test_frame_process_publishes_results_for_escape(frame_process):
    bsread = pytest.importorskip("bsread")
    client = frame_process
    client.send(type="demo", rate=20.0)
    client.send(type="settings", fit=True)
    client.send(type="publish")
    message, _ = client.receive("published")
    host, port = message["address"].replace("tcp://", "").rsplit(":", 1)
    assert host == "127.0.0.1"
    with bsread.Source(host=host, port=int(port), mode=bsread.SUB, receive_timeout=3000) as source:
        received = source.receive()
    data = received.data.data
    assert {"x_com", "fit_x_mean", "fit_y_sigma"} <= set(data)
    assert received.data.pulse_id > 0


def test_frame_process_ends_with_its_parent(tmp_path):
    pytest.importorskip("zmq")
    # a parent that starts the frame process and exits at once
    code = (
        "import subprocess, sys; "
        f"import os; p = subprocess.Popen([sys.executable, {cf.__file__!r}, 'ipc://{tmp_path}/nobody', "
        "str(os.getpid())], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print(p.pid)"
    )
    pid = int(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, 9)
    pytest.fail("the frame process outlived its parent")


def test_paused_frame_process_sends_nothing_new_until_resumed(frame_process):
    client = frame_process
    client.send(type="demo", rate=20.0)
    first, _ = client.receive("frame")
    client.send(type="pause", value=True)
    time.sleep(0.3)
    while client.socket.poll(0):  # what was under way before the pause
        client.socket.recv_multipart()
    with pytest.raises(TimeoutError):
        client.receive("frame", timeout=0.6)
    # a changed setting still shows on the frame already there
    client.send(type="settings", colormap="viridis")
    message, (image,) = client.receive("frame")
    assert len(image) == 300 * 400 * 3
    client.send(type="pause", value=False)
    later, _ = client.receive("frame")
    assert later["pulse_id"] > message["pulse_id"]
