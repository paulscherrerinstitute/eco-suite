"""eco.feedback: loops on real eco element objects, in-process service,
HTTP server/client parity and the ipywidgets panel. No hardware."""

import socket
import time

import numpy as np
import pytest

from eco.elements.adjustable import AdjustableMemory
from eco.elements.detector import DetectorGet
from eco.feedback import PID, FeedbackManager
from eco.feedback.core import resolve
from eco.feedback.service import BadRequest, Conflict, NotFound


def wait_until(cond, timeout=5.0, step=0.02):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def plant():
    """det = 2 * act - 3, on eco's own element classes."""
    act = AdjustableMemory(0.0, name="fb_test_act")
    det = DetectorGet(lambda: 2 * act.get_current_value() - 3, name="fb_test_det")
    return act, det


@pytest.fixture
def mgr():
    m = FeedbackManager()
    yield m
    m.stop_all()


def add_loop(mgr, plant, **kw):
    act, det = plant
    kw.setdefault("rate_hz", 100)
    # the output is a correction *rate*: kp integrates, ki alone would oscillate
    kw.setdefault("pid", PID(kp=10.0, ki=0.0, out_min=-50, out_max=50))
    return mgr.add("loop", det, act, **kw)


def test_loop_converges_through_eco_changer(mgr, plant):
    act, det = plant
    add_loop(mgr, plant, setpoint=5.0)
    mgr.service.set("loop", enabled=True)
    assert wait_until(lambda: abs(det.get_current_value() - 5.0) < 1e-3)
    assert act.get_current_value() == pytest.approx(4.0, abs=1e-3)
    st = mgr.service.status("loop")
    assert st["error"] is None
    assert st["detector_name"] == "fb_test_det"
    assert st["adjustable_name"] == "fb_test_act"


def test_registry_uses_eco_names(mgr, plant):
    add_loop(mgr, plant)
    assert mgr.service.detectors() == ["fb_test_det"]
    assert mgr.service.actuators() == ["fb_test_act"]


def test_disabled_loop_monitors_without_writing(mgr, plant):
    act, _ = plant
    add_loop(mgr, plant, setpoint=5.0)
    assert wait_until(lambda: len(mgr.loops["loop"].history) > 5)
    assert act.get_current_value() == 0.0
    assert mgr.service.status("loop")["last_value"] == -3.0


@pytest.mark.parametrize("bad", [None, float("nan"), [1.0, 2.0]])
def test_failed_reads_are_skipped_and_recover(mgr, plant, bad):
    act, det = plant
    good = det._get
    add_loop(mgr, plant, setpoint=5.0)
    mgr.service.set("loop", enabled=True)
    det._get = lambda: bad
    assert wait_until(lambda: mgr.service.status("loop")["error"] is not None)
    frozen = act.get_current_value()
    time.sleep(0.1)
    assert act.get_current_value() == frozen  # nothing written on garbage
    assert mgr.loops["loop"]._thread.is_alive()
    det._get = good
    assert wait_until(lambda: mgr.service.status("loop")["error"] is None)
    assert wait_until(lambda: abs(det.get_current_value() - 5.0) < 1e-3)
    messages = [e["message"] for e in mgr.service.logs()]
    assert sum("cycle failed" in m for m in messages) == 1  # logged once, not per cycle
    assert any("working again" in m for m in messages)


def test_blocked_write_is_an_error_not_a_crash(mgr):
    class Blocked:
        name = "blocked_act"

        def get_current_value(self):
            return 0.0

        def set_target_value(self, value):
            raise PermissionError("claimed by a queue task")

    mgr.add("loop", lambda: 1.0, Blocked(), rate_hz=100, pid=PID(kp=1.0))
    mgr.service.set("loop", enabled=True)
    assert wait_until(lambda: "claimed" in (mgr.service.status("loop")["error"] or ""))
    assert mgr.loops["loop"]._thread.is_alive()


def test_interlock_holds_and_resumes(mgr, plant):
    act, det = plant
    permit = AdjustableMemory(1, name="fb_test_permit")
    mgr.add_interlock("permit", permit)
    add_loop(mgr, plant, setpoint=5.0, interlocks=["permit"])
    permit.set_target_value(0).wait()
    mgr.service.set("loop", enabled=True)
    assert wait_until(lambda: mgr.service.status("loop")["held"])
    assert act.get_current_value() == 0.0
    permit.set_target_value(1).wait()
    assert wait_until(lambda: abs(det.get_current_value() - 5.0) < 1e-3)
    assert not mgr.service.status("loop")["held"]


def test_resolve_dotted_names_from_session(monkeypatch, plant):
    import __main__

    act, _ = plant

    class Box:
        pass

    box = Box()
    box.act = act
    monkeypatch.setattr(__main__, "fb_test_box", box, raising=False)
    assert resolve("fb_test_box.act") is act
    with pytest.raises(NameError):
        resolve("fb_test_not_defined.act")
    m = FeedbackManager()
    try:
        m.add_actuator("fb_test_box.act")
        assert m.adjustables["fb_test_box.act"] is act
    finally:
        m.stop_all()


def test_service_validation(mgr, plant):
    add_loop(mgr, plant)
    svc = mgr.service
    with pytest.raises(BadRequest):
        svc.set("loop", kpp=1)
    with pytest.raises(BadRequest):
        svc.set("loop", rate_hz=0)
    with pytest.raises(NotFound):
        svc.status("nope")
    with pytest.raises(NotFound):
        svc.start_response_matrix("m", ["nope"], ["fb_test_det"])
    with pytest.raises(NotFound):
        svc.create_matrix_loop("ml", "no_matrix")


def test_response_matrix_and_matrix_loop_on_eco_objects(mgr):
    acts = [AdjustableMemory(0.0, name=f"fb_mx_act{i}") for i in range(2)]
    R = np.array([[1.0, 0.3], [0.2, -1.0]])
    dets = [DetectorGet(lambda i=i: float(R[i] @ [a.get_current_value() for a in acts]),
                        name=f"fb_mx_det{i}") for i in range(2)]
    for a in acts:
        mgr.add_actuator(a.name, a)
    for d in dets:
        mgr.add_detector(d.name, d)
    svc = mgr.service
    svc.start_response_matrix("m", [a.name for a in acts], [d.name for d in dets],
                              step=0.1, settle_s=0.0, repeats=1)
    with pytest.raises(Conflict):
        svc.start_response_matrix("m", [a.name for a in acts], [d.name for d in dets])
    assert wait_until(lambda: svc.response_matrix_status("m")["state"] == "done")
    np.testing.assert_allclose(svc.response_matrix_status("m")["matrix"], R, atol=1e-9)
    # baseline restored
    assert [a.get_current_value() for a in acts] == [0.0, 0.0]

    svc.create_matrix_loop("ml", "m", gain=0.5, rate_hz=100)
    svc.set_matrix_loop("ml", reference={"fb_mx_det0": 1.0, "fb_mx_det1": -1.0}, enabled=True)
    assert wait_until(lambda: abs(dets[0].get_current_value() - 1.0) < 1e-4
                      and abs(dets[1].get_current_value() + 1.0) < 1e-4)


def test_config_roundtrip(mgr, plant, tmp_path):
    pytest.importorskip("yaml")
    add_loop(mgr, plant, setpoint=1.0)
    mgr.service.set("loop", setpoint=2.5, kp=0.1)
    path = tmp_path / "fb.yaml"
    mgr.service.save_config(str(path))
    mgr.service.set("loop", setpoint=0.0, kp=0.0)
    mgr.service.load_config(str(path))
    st = mgr.service.status("loop")
    assert (st["setpoint"], st["kp"]) == (2.5, 0.1)


def test_stop_all_disables_and_stops(mgr, plant):
    add_loop(mgr, plant)
    mgr.service.set("loop", enabled=True)
    mgr.stop_all()
    assert not mgr.loops["loop"].enabled
    assert not mgr.loops["loop"]._thread.is_alive()


# -- HTTP ------------------------------------------------------------------

def test_server_routes_match_service(mgr, plant):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from eco.feedback.server import make_app

    add_loop(mgr, plant, setpoint=1.0)
    http = TestClient(make_app(mgr, api_key="k"))
    assert list(http.get("/loops").json()) == ["loop"]
    assert http.get("/loops/loop").json()["detector_name"] == "fb_test_det"
    assert http.get("/actuators").json() == mgr.service.actuators()
    assert http.patch("/loops/loop", json={"setpoint": 2.0}).status_code == 401
    r = http.patch("/loops/loop", json={"setpoint": 2.0}, headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.json()["setpoint"] == 2.0
    assert mgr.loops["loop"].setpoint == 2.0
    r = http.get("/loops/nope")
    assert r.status_code == 404 and "nope" in r.json()["detail"]
    r = http.patch("/loops/loop", json={"rate_hz": -1}, headers={"X-API-Key": "k"})
    assert r.status_code == 400
    assert http.get("/health").json()["status"] == "ok"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_background_server_and_client(mgr, plant):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from eco.feedback.client import FeedbackClient

    _, det = plant
    add_loop(mgr, plant, setpoint=5.0)
    srv = mgr.serve(port=_free_port())
    try:
        c = FeedbackClient(srv.url)
        c.set("loop", enabled=True)
        assert wait_until(lambda: abs(det.get_current_value() - 5.0) < 1e-3)
        assert len(c.history("loop", n=10)["t"]) == 10
        with pytest.raises(NotFound):
            c.status("nope")
        with pytest.raises(BadRequest):
            c.set("loop", rate_hz=-1)
    finally:
        srv.stop()
    assert not srv.running


# -- panel -------------------------------------------------------------------

def test_panel_in_process_uses_no_pyplot(mgr, plant):
    pytest.importorskip("ipywidgets")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from eco.feedback.gui import FeedbackPanel

    add_loop(mgr, plant)
    figs_before = plt.get_fignums()
    panel = FeedbackPanel(client=mgr.service, refresh_s=0.05)
    try:
        assert wait_until(lambda: len(panel.plots[0].output.value) > 1000)
        assert wait_until(lambda: bool(panel.log_panel.widget.outputs))
        assert "fb_test_det" in panel.tabs[0].readback.value
        assert plt.get_fignums() == figs_before
        panel.tabs[0].setpoint.value = 3.0  # widget edits go to the loop
        assert mgr.loops["loop"].setpoint == 3.0
    finally:
        panel.stop(join=True)
    assert not panel._thread.is_alive()


def test_qt_panel_in_process(mgr, plant):
    pytest.importorskip("qtpy")
    from qtpy import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    act, det = plant
    add_loop(mgr, plant, setpoint=5.0)
    panel = mgr.qt_panel(refresh_s=0.05)

    def pump(cond, timeout=5.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    try:
        assert pump(lambda: panel.table.rowCount() == 1
                    and panel.table.item(0, 2).text() == "off")
        assert "fb_test_det" in panel.table.item(0, 1).text()
        assert pump(lambda: panel._selected == "loop"
                    and panel.fields["setpoint"].text() == "5")
        panel.btn_enable.click()  # the write runs in a thread, not in the slot
        assert pump(lambda: abs(det.get_current_value() - 5.0) < 1e-3)
        assert pump(lambda: panel.table.item(0, 2).text() == "ON")
        panel.fields["setpoint"].setText("1")
        panel._apply()
        assert pump(lambda: mgr.loops["loop"].setpoint == 1.0)
        panel.btn_disable_all.click()
        assert pump(lambda: not mgr.loops["loop"].enabled)
        assert pump(lambda: "enabled" in panel._log.toPlainText()
                    or panel._log.blockCount() > 1)
    finally:
        panel.stop(join=True)
    assert not panel._thread.is_alive()
