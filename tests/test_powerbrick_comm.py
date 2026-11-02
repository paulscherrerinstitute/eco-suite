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
        self.jumps = []
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
        def connect(
            self, host, password="deltatau", timeout=30.0, jump=None, jump_socket=None
        ):
            time.sleep(0.05)
            ctrl.connects += 1
            ctrl.jumps.append((jump, jump_socket))
            self.client = type("C", (), {"close": lambda self: None})()
            self.chan = FakeChannel(ctrl)

    monkeypatch.setattr(pb, "SSHComm", FakeSSHComm)
    monkeypatch.setattr(pb, "_reachable", lambda host: not host.startswith("SAROP"))
    monkeypatch.setattr(pb, "_jump_socket", None)
    monkeypatch.setattr(pb, "_jump_user", None)
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
    # 400 parameters: each axis registers its parameters on its first read,
    # which costs one more batch -- 8 axes, not 400 round trips
    assert len(controller.writes) <= 12


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


def test_axis_from_out_wins_hardcoded_is_fallback(controller, monkeypatch):
    out = {"SARES21-XRD:MOT_YU": 2, "SARES21-XRD:MOT_X": None}
    monkeypatch.setattr(pb, "axis_from_out", lambda pvname: out[pvname])
    yu = pb.PowerBrickChannelPars(
        "SARES21-CPPM-XRD4", axis=1, type="motor", pvname="SARES21-XRD:MOT_YU", name="yu"
    )
    assert yu.Motor_pos.get_current_value() == 2.5
    assert (yu.axis, yu.axis_source) == (2, "OUT")
    x = pb.PowerBrickChannelPars(
        "SARES21-CPPM-XRD4", axis=3, type="motor", pvname="SARES21-XRD:MOT_X", name="x"
    )
    assert x.Motor_pos.get_current_value() == 3.5
    assert (x.axis, x.axis_source) == (3, "fallback")


def test_host_behind_a_gateway_comes_from_iocinfo(controller, monkeypatch):
    monkeypatch.setattr(pb, "axis_from_out", lambda pvname: 6)
    looked_up = []

    def ioc_host(pvname):
        looked_up.append(pvname)
        return "SAROP21-CPPM-MOT7052"

    monkeypatch.setattr(pb, "ioc_host", ioc_host)
    bu = pb.PowerBrickChannelPars(
        lambda: "sf-saresb-cagw-01.psi.ch", type="motor",
        pvname="SAROP21-OKBV139:BU", name="bu",
    )
    assert controller.connects == 0 and looked_up == []
    _open_gateway(monkeypatch)
    assert bu.host == "SAROP21-CPPM-MOT7052"
    assert looked_up == ["SAROP21-OKBV139:BU"]
    # the CA server is used as it is when it is a PowerBrick
    tx = pb.PowerBrickChannelPars(
        lambda: "SARES21-CPPM-XRD4.psi.ch", type="motor",
        pvname="SARES21-XRD:MOT_TX", name="tx",
    )
    assert tx.host == "SARES21-CPPM-XRD4.psi.ch"
    assert looked_up == ["SAROP21-OKBV139:BU"]


def _open_gateway(monkeypatch):
    monkeypatch.setattr(pb, "_jump_socket", "/run/gw.sock")
    monkeypatch.setattr(pb, "_jump_user", "someone")
    monkeypatch.setattr(pb, "gateway_open", lambda: True)


def test_unreachable_controller_needs_the_gateway_login(controller, monkeypatch):
    pb.get_power_brick_comm("SARES21-CPPM-XRD4").connect()
    with pytest.raises(PermissionError, match="unlock"):
        pb.get_power_brick_comm("SAROP21-CPPM-MOT7052").connect()
    _open_gateway(monkeypatch)
    pb.get_power_brick_comm("SAROP21-CPPM-MOT7052").connect()
    assert controller.jumps == [
        (None, None),
        (pb.JUMP_HOST, "/run/gw.sock"),
    ]


def test_behind_gateway_is_locked_until_unlocked(controller, monkeypatch):
    monkeypatch.setattr(pb, "axis_from_out", lambda pvname: 6)
    monkeypatch.setattr(pb, "ioc_host", lambda pvname: "SAROP21-CPPM-MOT7052")
    bu = pb.PowerBrickChannelPars(
        lambda: "sf-saresb-cagw-01.psi.ch", type="motor",
        pvname="SAROP21-OKBV139:BU", name="bu",
    )
    # locked: the key instead of the parameters, nothing contacted
    assert bu.locked
    assert bu.status_collection.get_list() == []
    assert bu.get_current_value().startswith("\U0001F511")
    assert "unlock()" in repr(bu)
    with pytest.raises(PermissionError):
        bu.Motor_pos.get_current_value()
    assert controller.connects == 0
    _open_gateway(monkeypatch)
    assert not bu.locked
    assert len(bu.status_collection.get_list()) == len(pb.pars_motors)
    assert bu.get_current_value().startswith("\U0001F513")
    assert bu.Motor_pos.get_current_value() == 6.5
    assert controller.jumps == [(pb.JUMP_HOST, "/run/gw.sock")]


def test_directly_reachable_controller_is_not_gated(controller):
    tx = pb.PowerBrickChannelPars(
        lambda: "SARES21-CPPM-XRD4.psi.ch", axis=3, type="motor", name="tx"
    )
    assert not tx.locked
    assert not hasattr(tx, "get_current_value")
    assert len(tx.status_collection.get_list()) == len(pb.pars_motors)


def test_unlock_asks_whose_login_on_a_shared_account(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "_jump_socket", None)
    monkeypatch.setattr(pb, "gateway_open", lambda: pb._jump_socket is not None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(pb.getpass, "getuser", lambda: "gac-bernina")
    monkeypatch.setattr("builtins.input", lambda prompt="": "mankowsky_r")
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(pb.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    pb.unlock()
    login = calls[0]
    assert login[-1] == "mankowsky_r@" + pb.JUMP_HOST
    sock = pb._jump_socket
    assert f"ControlPath={sock}" in login
    assert (tmp_path / ".cache" / "eco").stat().st_mode & 0o077 == 0
    pb.lock()
    assert calls[-1][:4] == ["ssh", "-S", sock, "-O"]
    assert pb._jump_socket is None


def test_failed_login_leaves_it_locked(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "_jump_socket", None)
    monkeypatch.setenv("HOME", str(tmp_path))

    class Failed:
        returncode = 255

    monkeypatch.setattr(pb.subprocess, "run", lambda cmd, **kw: Failed())
    with pytest.raises(PermissionError):
        pb.unlock(user="someone")
    assert pb._jump_socket is None


@pytest.mark.parametrize(
    "out, axis",
    [("@asyn(PPMAC1,6)", 6), ("@asyn(PPMAC1, 10)", 10), ("", None), ("SOME:PV", None)],
)
def test_out_link_parsing(out, axis):
    m = pb._OUT_AXIS.match(out)
    assert (int(m.group(1)) if m else None) == axis


def test_paramiko_handshake_logging_is_quiet():
    assert logging.getLogger("paramiko").getEffectiveLevel() >= logging.WARNING


def test_offline_record_fails_fast_then_retries(controller, monkeypatch):
    lookups = []
    out = {"axis": None}

    def axis_from_out(pvname):
        lookups.append(pvname)
        return out["axis"]

    monkeypatch.setattr(pb, "axis_from_out", axis_from_out)
    m = pb.PowerBrickChannelPars(
        "SARES21-CPPM-XRD1", type="motor", pvname="SARES21-XRD:MOT_KAP_KRX", name="m"
    )
    for item in m.status_collection.get_list():
        with pytest.raises(ValueError, match="no PowerBrick axis"):
            item.get_current_value()
    assert len(lookups) == 1  # not once per parameter
    m._resolve_failed = (0.0, m._resolve_failed[1])  # retry period over
    out["axis"] = 1
    assert m.Motor_pos.get_current_value() == 1.5
    assert len(lookups) == 2
