"""eco.devices_general.powerbrick: no SSH session until the first read, one
session per controller, reads from many threads (get_status) get their own
replies, and status reads of all axes share batched requests."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from eco.devices_general import powerbrick as pb
from eco.devices_general.PBComm import SSHComm

ACK = SSHComm.gpascii_ack


class FakeController:
    """What one PowerBrick answers: `Name=value` + ACK per line, an error
    line + ACK for unknown names (as observed on SARES21-CPPM-XRD4)."""

    def __init__(self):
        self.values = {}
        self.connects = 0
        self.writes = []

    def reply(self, line):
        name, eq, value = line.partition("=")
        if eq:
            self.values[name.lower()] = value
            return ACK
        if name.lower() not in self.values:
            return f"stdin:1:1: error #21: ILLEGAL PARAMETER: {name}\r\n" + ACK
        return f"{name}={self.values[name.lower()]}\r\n" + ACK


class FakeChannel:
    def __init__(self, controller):
        self.controller = controller
        self._out = ""
        self._lock = threading.Lock()

    def send(self, data):
        self.controller.writes.append(data)
        out = "".join(self.controller.reply(l) for l in data.splitlines() if l)
        with self._lock:
            self._out += out

    def recv_ready(self):
        return bool(self._out)

    def recv(self, n):
        # like the real channel: replies arrive in pieces, not per request
        with self._lock:
            chunk, self._out = self._out[:7], self._out[7:]
        return chunk.encode()


@pytest.fixture
def controller(monkeypatch):
    ctrl = FakeController()

    class FakeSSHComm(SSHComm):
        def connect(self, host, password="deltatau", timeout=30.0):
            time.sleep(0.05)
            ctrl.connects += 1
            self.client = type("C", (), {"close": lambda self: None})()
            self.chan = FakeChannel(ctrl)

    monkeypatch.setattr(pb, "SSHComm", FakeSSHComm)
    monkeypatch.setattr(pb, "connected_power_bricks", {})
    for axis in range(1, 9):
        ctrl.values[f"motor[{axis}].pos"] = f"{axis}.5"
        for par, mode in pb.pars_motors:
            name = par.format(axis).split(".", 1)[1]
            if name != "pos":
                ctrl.values[par.format(axis).lower()] = str(axis * 10)
    return ctrl


def test_building_does_not_connect_and_one_session_per_controller(controller):
    with ThreadPoolExecutor(8) as ex:
        pars = list(
            ex.map(
                lambda a: pb.PowerBrickChannelPars(
                    "host1", axis=a, type="motor", name=f"m{a}"
                ),
                range(1, 9),
            )
        )
    assert controller.connects == 0
    pars[0].Motor_pos  # noqa: B018 -- attribute exists, reading it is lazy
    assert controller.connects == 0
    assert pars[2].Motor_pos.get_current_value() == 3.5
    assert controller.connects == 1


def test_virtual_axis_never_connects(controller):
    pb.PowerBrickChannelPars("host1", axis=9, type="virtual", name="v")
    assert controller.connects == 0


def test_parallel_status_reads_are_correct_and_batched(controller):
    pars = [
        pb.PowerBrickChannelPars("host1", axis=a, type="motor", name=f"m{a}")
        for a in range(1, 9)
    ]
    items = [
        (a, item)
        for a, p in enumerate(pars, start=1)
        for item in p.status_collection.get_list()
        if item is not p
    ]
    assert len(items) == 8 * len(pb.pars_motors)

    def read(ai):
        a, item = ai
        return a, item.alias.get_full_name(base=pars[a - 1]), item.get_current_value()

    with ThreadPoolExecutor(20) as ex:
        results = list(ex.map(read, items))
    for a, name, value in results:
        assert value == (a + 0.5 if name == "Motor_pos" else a * 10), name
    # 408 parameters, read in batches of PowerBrickComm.batch_size
    assert len(controller.writes) <= 3


def test_set_consumes_ack_and_invalidates_snapshot(controller):
    p = pb.PowerBrickChannelPars("host1", axis=3, type="motor", name="m3")
    assert p.Motor_pos.get_current_value() == 3.5
    comm = p._pbcom
    comm.set_parameter(7, "Motor[3].pos")
    assert p.Motor_pos.get_current_value() == 7
    # an unregistered read right after a set must not get the set's ACK
    controller.values["motor[3].jogspeed"] = "12.5"
    assert comm.get_parameter("Motor[3].JogSpeed") == 12.5


def test_error_reply_raises_for_that_parameter_only(controller):
    comm = pb.get_power_brick_comm("host1")
    comm.register(["Motor[1].Pos", "Motor[1].Bogus", "Motor[2].Pos"])
    with pytest.raises(ValueError, match="ILLEGAL PARAMETER"):
        comm.get_parameter("Motor[1].Bogus", max_age=1)
    assert comm.get_parameter("Motor[1].Pos", max_age=1) == 1.5
    assert comm.get_parameter("Motor[2].Pos", max_age=1) == 2.5


def test_paramiko_handshake_logging_is_quiet():
    assert logging.getLogger("paramiko").getEffectiveLevel() >= logging.WARNING
