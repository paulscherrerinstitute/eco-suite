"""eco.web.client_session: the protocol, without a transport -- requests
answered off the caller's thread, coalesced values, writes only when
enabled, Stop during a move."""
import queue
import time

import pytest

from eco.web.backends import SessionBackend
from eco.web.client_session import ClientSession
from eco.web.demo import DemoNamespace


class Client:
    def __init__(self, writes=False):
        self.messages = queue.Queue()
        self.session = ClientSession(SessionBackend(DemoNamespace()), self.messages.put,
                                     writes=writes, poll_interval=0.05)
        self.next_id = 0
        self.backlog = []  # events that arrived while waiting for a reply

    def call(self, op, timeout=5.0, **args):
        self.next_id += 1
        request_id = self.next_id
        self.session.handle({"v": 0, "id": request_id, "op": op, "args": args})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.messages.get(timeout=timeout)
            if message.get("re") == request_id:
                return message
            if "ev" in message:
                self.backlog.append(message)
        raise AssertionError(f"no reply to {op}")

    def events(self, kind, seconds=1.0):
        found = [m["data"] for m in self.backlog if m["ev"] == kind]
        self.backlog = [m for m in self.backlog if m["ev"] != kind]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if message.get("ev") == kind:
                found.append(message["data"])
        return found

    def values(self, name, seconds=2.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            event = self.session.flush()
            if event and name in event["data"]:
                return event["data"][name]
            time.sleep(0.02)
        raise AssertionError(f"no value for {name}")


@pytest.fixture
def client():
    c = Client()
    yield c
    c.session.close()


@pytest.fixture
def writer():
    c = Client(writes=True)
    yield c
    c.session.close()


def test_hello_and_names(client):
    hello = client.call("hello", client_version="t")
    assert hello["ok"] and hello["data"]["writes"] is False and hello["data"]["label"] == "demo"
    names = client.call("names")["data"]
    assert "mono" in names["initialized"]


def test_unknown_op_and_bad_arguments_are_errors(client):
    reply = client.call("frobnicate")
    assert not reply["ok"] and reply["error"]["type"] == "ProtocolError"
    reply = client.call("describe", nonsense=1)
    assert not reply["ok"] and reply["error"]["type"] == "ProtocolError"


def test_subscribe_needs_a_described_name(client):
    reply = client.call("subscribe", names=["mono.energy"])
    assert not reply["ok"] and reply["error"]["type"] == "NotFound"


def test_values_arrive_coalesced_after_subscribe(client):
    client.call("describe", name="mono")
    assert client.call("subscribe", names=["mono.energy", "mono.pitch_rbv"])["ok"]
    value = client.values("mono.energy")
    assert value["num"] == 7.0 and value["state"] == "polled" and value["t"] > 0
    time.sleep(0.3)  # several polls at 0.05 s ...
    event = client.session.flush()
    assert event["ev"] == "values" and "now" in event
    assert set(event["data"]) <= {"mono.energy", "mono.pitch_rbv"}  # ... one entry each


def test_unsubscribed_items_stop_reporting(client):
    client.call("describe", name="mono")
    client.call("subscribe", names=["mono.pitch_rbv"])
    client.values("mono.pitch_rbv")
    client.call("unsubscribe", names=["mono.pitch_rbv"])
    time.sleep(0.2)
    client.session.flush()
    time.sleep(0.3)
    event = client.session.flush()
    assert event is None or "mono.pitch_rbv" not in event["data"]


def test_writes_are_refused_by_default(client):
    client.call("describe", name="mono")
    for op, args in [("set", {"value": 8}), ("step", {"sign": 1, "step": 0.1}), ("stop", {}), ("trigger", {})]:
        name = "mono.home_all" if op == "trigger" else "mono.energy"
        reply = client.call(op, name=name, **args)
        assert not reply["ok"] and reply["error"]["type"] == "WritesDisabled", op
    rows = client.call("describe", name="mono")["data"]["rows"]
    assert not any(r["writable"] for r in rows)


def test_set_reports_moving_then_done(writer):
    writer.call("describe", name="lxt")
    writer.call("subscribe", names=["lxt"])
    writer.values("lxt")
    assert writer.call("set", name="lxt", value="1e-13")["ok"]
    states = [e["state"] for e in writer.events("write", seconds=1.0)]
    assert states[:2] == ["busy", "moving"] and states[-1] == "idle"


def test_set_errors_come_back_as_events(writer):
    writer.call("describe", name="mono")
    writer.call("subscribe", names=["mono.energy"])
    writer.values("mono.energy")
    writer.call("set", name="mono.energy", value=99)  # outside the simulated limits
    errors = [e for e in writer.events("write", seconds=0.5) if e["state"] == "error"]
    assert errors and "outside limits" in errors[0]["message"]


def test_stop_during_a_move(writer):
    writer.call("describe", name="slits")
    writer.call("subscribe", names=["slits.hgap"])
    writer.values("slits.hgap")
    writer.call("set", name="slits.hgap", value=9.0)  # 16 s at 0.5 mm/s
    time.sleep(0.3)
    assert writer.call("stop", name="slits.hgap")["ok"]
    states = [e["state"] for e in writer.events("write", seconds=1.0)]
    assert states[-1] == "idle"
    assert writer.session._objects["slits.hgap"].get_current_value() < 2.0


def test_step_and_enum_set(writer):
    writer.call("describe", name="vacuum")
    writer.call("subscribe", names=["vacuum.valve3"])
    assert writer.values("vacuum.valve3")["text"] == "closed"
    writer.call("set", name="vacuum.valve3", value="open")
    writer.events("write", seconds=1.2)
    assert writer.session._objects["vacuum.valve3"].get_current_value().name == "open"

    writer.call("describe", name="mono")
    step = writer.call("step_size", name="mono.harmonic")["data"]["step"]
    assert step == 1
    writer.call("step", name="mono.harmonic", sign=1, step=step)
    writer.events("write", seconds=0.3)
    assert writer.session._objects["mono.harmonic"].get_current_value() == 4


def test_writes_can_be_limited_to_names():
    c = Client(writes={"mono.harmonic"})
    try:
        rows = {r["name"]: r["writable"] for r in c.call("describe", name="mono")["data"]["rows"]}
        assert rows["mono.harmonic"] and not rows["mono.energy"]
        assert c.call("set", name="mono.energy", value=8)["error"]["type"] == "WritesDisabled"
    finally:
        c.session.close()
