"""The /handoff routes and the store behind them (client-write mode).

Two halves, matching the code: HandoffStore on its own (a small piece of
bookkeeping with a TTL and a spool directory, worth testing without HTTP in
the way), then the routes against a real NamespaceMonitorStore built on the
same fakes test_status_server.py uses.

What these are really guarding is that the client-write path produces the
*same files* as the server-write path it is an alternative to - a status
block with the same keys, an alias list of the same shape, and an h5 file
h5py can open - and that nothing it adds changes the existing routes.
"""

import json
import threading
import time

import numpy as np
import pytest

from eco.status_server.config import NamespaceServerConfig
from eco.status_server.handoff import (
    HandoffCapacity,
    HandoffError,
    HandoffStore,
    encode_recording_npz,
)
from eco.status_server.namespace_server import create_namespace_app

# Reused rather than re-fabricated: the point of these tests is the handoff
# layer, and a second set of namespace fakes drifting from the first one
# would make a failure here mean nothing.
from test_status_server import (  # noqa: F401 (fixtures)
    FakeMonitor,
    FakeNamespace,
    FakeStatusCollection,
    MonitorableDetector,
    fake_module,
    recording_store,
)


@pytest.fixture
def store(tmp_path):
    s = HandoffStore(spool_dir=tmp_path / "spool", janitor=False)
    yield s
    s.close()


# --------------------------------------------------------------------------
# HandoffStore


def test_a_slot_starts_pending_and_becomes_ready(store):
    slot = store.create("status", "status.json", "application/json")
    assert slot.state == "pending"
    with pytest.raises(HandoffError):
        slot.open()

    store.complete_bytes(slot.id, b'{"a": 1}')
    assert slot.state == "ready"
    assert slot.n_bytes == 8
    fh, n = slot.open()
    try:
        assert fh.read() == b'{"a": 1}'
        assert n == 8
    finally:
        fh.close()


def test_wait_returns_as_soon_as_the_producer_completes(store):
    slot = store.create("status", "status.json")

    def _produce():
        time.sleep(0.05)
        store.complete_bytes(slot.id, b"x" * 10)

    threading.Thread(target=_produce, daemon=True).start()
    assert slot.wait(timeout=5) is True
    assert slot.state == "ready"


def test_wait_times_out_without_claiming_the_slot_is_ready(store):
    slot = store.create("status", "status.json")
    assert slot.wait(timeout=0.05) is False
    assert slot.state == "pending"


def test_a_failed_producer_is_reported_not_swallowed(store):
    slot = store.create("status", "status.json")
    store.fail(slot.id, "ValueError: nope")
    assert slot.state == "error"
    assert slot.wait(timeout=0.1) is True
    with pytest.raises(HandoffError, match="nope"):
        slot.open()


def test_release_removes_the_spool_file(store, tmp_path):
    slot = store.create("recording", "r.h5")
    spool = store.slot_spool_dir(slot.id) / "r.h5"
    spool.write_bytes(b"0123456789")
    store.complete_file(slot.id, spool)
    assert slot.n_bytes == 10

    store.release(slot.id)
    assert not spool.exists()
    with pytest.raises(KeyError):
        store.get(slot.id)


def test_expired_slots_are_purged_with_their_files(store):
    slot = store.create("recording", "r.h5", ttl=0.01)
    spool = store.slot_spool_dir(slot.id) / "r.h5"
    spool.write_bytes(b"data")
    store.complete_file(slot.id, spool)

    time.sleep(0.02)
    assert store.purge_expired() == [slot.id]
    assert not spool.exists()
    assert store.stats()["n_slots"] == 0


def test_the_slot_cap_refuses_rather_than_dropping_somebody_elses_result(tmp_path):
    s = HandoffStore(spool_dir=tmp_path / "spool", max_slots=1, janitor=False)
    first = s.create("status", "a.json")
    with pytest.raises(HandoffCapacity):
        s.create("status", "b.json")
    # the existing one is untouched
    assert s.get(first.id) is first
    s.close()


def test_clean_spool_dir_drops_leftovers_from_a_previous_process(store):
    stale = store.spool_dir / "old"
    stale.mkdir(parents=True)
    (stale / "x.h5").write_bytes(b"...")
    assert store.clean_spool_dir() == 1
    assert not stale.exists()


def test_npz_encoding_round_trips_values_and_timestamps():
    t0 = time.time()
    rec = {
        "started_at": t0,
        "stopped_at": t0 + 1,
        "data": {
            "fake.a": {"values": [1.0, 2.0], "timestamps": [t0, t0 + 0.5]},
            "fake.s": {"values": ["Open", "Shut"], "timestamps": [t0, t0 + 0.5]},
        },
    }
    blob = encode_recording_npz(rec)

    import io

    with np.load(io.BytesIO(blob)) as z:
        meta = json.loads(bytes(z["__meta__"]).decode())
        assert set(meta["aliases"]) == {"fake.a", "fake.s"}
        assert z["v:fake.a"].tolist() == [1.0, 2.0]
        assert z["t:fake.a"].shape == (2,)
        assert [s.decode() if isinstance(s, bytes) else s
                for s in z["v:fake.s"].tolist()] == ["Open", "Shut"]
    assert rec["write_report"]["n_written"] == 2


# --------------------------------------------------------------------------
# routes


@pytest.fixture
def app_client(recording_store):
    config = NamespaceServerConfig(module_name=recording_store.module_name)
    app = create_namespace_app(config, store=recording_store)
    app.config["TESTING"] = True
    yield app.test_client(), recording_store, app
    app.config["ECO_HANDOFF"].close()


def _collect(client, handoff_id, wait=5, release=True):
    suffix = f"?wait={wait}" + ("&release=1" if release else "")
    resp = client.get(f"/handoff/{handoff_id}/data{suffix}")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    return resp


def test_status_handoff_delivers_the_same_block_a_capture_would_write(app_client):
    client, store, _ = app_client
    started = client.post("/handoff/status", json={"key": "status_run_start"})
    assert started.status_code == 202
    body = started.get_json()
    assert body["handoff_id"] and body["job_id"]

    resp = _collect(client, body["handoff_id"])
    doc = json.loads(resp.get_data())
    assert doc["key"] == "status_run_start"
    assert doc["block"]["status"]["fake.m1"] == 1.0
    assert doc["block"]["status_channels"]["fake.m1"] == "PV:M1"
    meta = json.loads(resp.headers["X-Eco-Handoff-Meta"])
    assert meta["n_status"] == len(doc["block"]["status"])


def test_a_collected_slot_is_gone_when_release_was_asked_for(app_client):
    client, _, _ = app_client
    hid = client.post("/handoff/status", json={}).get_json()["handoff_id"]
    _collect(client, hid, release=True)
    assert client.get(f"/handoff/{hid}").status_code == 404


def test_a_slot_survives_a_fetch_that_did_not_release_it(app_client):
    client, _, _ = app_client
    hid = client.post("/handoff/status", json={}).get_json()["handoff_id"]
    _collect(client, hid, release=False)
    info = client.get(f"/handoff/{hid}").get_json()
    assert info["n_fetches"] == 1
    assert client.delete(f"/handoff/{hid}").status_code == 200
    assert client.get(f"/handoff/{hid}").status_code == 404


def test_client_status_is_merged_into_the_block(app_client):
    client, _, _ = app_client
    started = client.post("/handoff/status", json={
        "client_status": {
            "status": {"scans.description": "a scan"},
            "status_channels": {"scans.description": None},
        },
    }).get_json()
    doc = json.loads(_collect(client, started["handoff_id"]).get_data())
    assert doc["block"]["status"]["scans.description"] == "a scan"
    assert doc["block"]["status"]["fake.m1"] == 1.0


def test_an_uncollected_pending_slot_answers_202_not_an_error(app_client):
    client, _, app = app_client
    handoffs = app.config["ECO_HANDOFF"]
    slot = handoffs.create("status", "status.json")
    resp = client.get(f"/handoff/{slot.id}/data")
    assert resp.status_code == 202
    assert resp.get_json()["status"] == "pending"


def test_unknown_handoff_is_a_404_everywhere(app_client):
    client, _, _ = app_client
    assert client.get("/handoff/nope").status_code == 404
    assert client.get("/handoff/nope/data").status_code == 404
    assert client.delete("/handoff/nope").status_code == 404


def test_aliases_handoff_delivers_the_alias_list(app_client):
    client, store, _ = app_client
    started = client.post("/handoff/aliases", json={}).get_json()
    aliases = json.loads(_collect(client, started["handoff_id"]).get_data())
    assert aliases == store.namespace.alias.get_all()
    assert all({"alias", "channel", "channeltype"} <= set(a) for a in aliases)


def test_recording_handoff_stops_the_recording_and_hands_over_an_h5(app_client, tmp_path):
    import h5py

    client, store, _ = app_client
    client.post("/recording/start", json={"recording_id": "r1"})
    FakeMonitor.instances[0].push(1.5)
    FakeMonitor.instances[0].push(2.5)

    started = client.post("/handoff/recording", json={
        "recording_id": "r1", "format": "h5",
        "filename": "namespace_monitor.ixp.h5",
    })
    assert started.status_code == 202
    body = started.get_json()
    # stopped before the response, as /recording/capture does - only the
    # encoding is backgrounded. Dropping its buffers happens with the
    # encoding, so it is still listed here, just no longer running.
    assert [r["running"] for r in store.list_recordings()] == [False]

    resp = _collect(client, body["handoff_id"])
    assert store.list_recordings() == []
    out = tmp_path / "namespace_monitor.ixp.h5"
    out.write_bytes(resp.get_data())
    with h5py.File(out) as f:
        assert "fake.m1" in f
        assert f["fake.m1/data_0000"].shape[0] >= 2


def test_recording_handoff_can_hand_over_raw_buffers_instead(app_client):
    client, _, _ = app_client
    client.post("/recording/start", json={"recording_id": "r2"})
    FakeMonitor.instances[0].push(7.0)

    body = client.post("/handoff/recording", json={
        "recording_id": "r2", "format": "json",
    }).get_json()
    doc = json.loads(_collect(client, body["handoff_id"]).get_data())
    assert 7.0 in doc["data"]["fake.m1"]["values"]
    assert doc["started_at"] <= doc["stopped_at"]


def test_recording_handoff_rejects_an_unknown_format(app_client):
    client, _, _ = app_client
    resp = client.post("/handoff/recording",
                       json={"recording_id": "r", "format": "parquet"})
    assert resp.status_code == 400


def test_recording_handoff_on_a_missing_recording_is_a_404(app_client):
    client, _, _ = app_client
    assert client.post("/handoff/recording",
                       json={"recording_id": "never"}).status_code == 404


def test_health_reports_the_handoff_store(app_client):
    client, _, _ = app_client
    body = client.get("/health").get_json()
    assert body["handoff"]["n_slots"] == 0
    assert body["handoff"]["max_slots"] > 0


def test_the_server_write_routes_are_untouched(app_client, tmp_path):
    """The whole feature is additive; /status/capture must still write the
    file itself."""
    client, _, app = app_client
    app.config["ECO_CONFIG"].data_root_pattern = str(tmp_path / "run{run_number:04d}")
    body = client.post("/status/capture",
                       json={"pgroup": "p1", "run_number": 3, "upload": False})
    assert body.status_code == 202
    job_id = body.get_json()["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        job = client.get(f"/status/job/{job_id}").get_json()["job"]
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert job["state"] == "done", job
    assert (tmp_path / "run0003" / "status.json").exists()


def test_a_long_skip_list_is_replaced_by_a_tally_not_dropped(app_client):
    """A recording that skips hundreds of channels must still say *why*.

    The per-channel list does not fit in a response header at that size, and
    the first version of this simply removed it - so a real run reporting
    "887 skipped" gave no way to find out what they had in common without
    going to the server. The header now carries a count per distinct reason
    instead, and stays parseable JSON.
    """
    client, _, _ = app_client
    hid = client.post("/handoff/status", json={}).get_json()["handoff_id"]
    store = client.application.config["ECO_HANDOFF"]
    slot = store.get(hid)
    slot.meta["skipped"] = {
        f"chan.{i}": ("TypeError: cannot store object dtype"
                      if i % 3 else "value type not storable in h5")
        for i in range(900)
    }

    resp = _collect(client, hid)
    meta = json.loads(resp.headers["X-Eco-Handoff-Meta"])
    assert meta["skipped_omitted"] is True
    assert "skipped" not in meta
    assert sum(meta["skipped_reasons"].values()) == 900
    assert meta["skipped_reasons"]["TypeError"] == 600
    assert meta["skipped_reasons"]["value type not storable in h5"] == 300
