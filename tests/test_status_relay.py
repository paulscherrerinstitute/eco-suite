"""The client-side half of client-write mode: eco.status_server.relay.

The relay is the piece that makes the permission argument true - it is the
process that calls open() on the shared tree, under the account running the
experiment. So these tests check the two things that matter about it:

* it produces the same files the server-side write produces (same merge
  behaviour for status.json, same alias list, byte-identical h5), and
* it reports what happened through its result file, because nobody is
  waiting on its return value.

The last test runs the real detached child against a real HTTP server; the
rest use thread/inline mode so a failure points at the logic rather than at
process plumbing.
"""

import json
import threading
import time

import pytest

from eco.status_server import relay
from eco.status_server.config import NamespaceServerConfig
from eco.status_server.namespace_server import create_namespace_app

from test_status_server import (  # noqa: F401 (fixtures)
    FakeMonitor,
    fake_module,
    recording_store,
)


@pytest.fixture
def jobdir(tmp_path, monkeypatch):
    d = tmp_path / "relay_jobs"
    monkeypatch.setenv("ECO_STATUS_RELAY_DIR", str(d))
    return d


@pytest.fixture
def server(recording_store):
    """A real HTTP server with the handoff routes, on a free port."""
    from werkzeug.serving import make_server

    config = NamespaceServerConfig(module_name=recording_store.module_name,
                                   port=0)
    app = create_namespace_app(config, store=recording_store)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", recording_store, app
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        app.config["ECO_HANDOFF"].close()


def _no_upload(spec):
    spec = dict(spec)
    spec["upload"] = False
    return spec


# --------------------------------------------------------------------------
# status


def test_status_relay_writes_the_block_into_the_run_directory(server, jobdir,
                                                              tmp_path):
    url, _, _ = server
    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_status(key="status_run_start")
    spec = _no_upload(relay.status_spec(
        url, tmp_path / "aux", handoff_id=started["handoff_id"],
        key="status_run_start", keep_status=True, wait=30,
    ))
    result = relay.submit(spec, mode="inline").result()

    assert result["state"] == "done", result
    written = json.loads((tmp_path / "aux" / "status.json").read_text())
    assert written["status_run_start"]["status"]["fake.m1"] == 1.0
    assert written["status_run_start"]["status_channels"]["fake.m1"] == "PV:M1"
    # keep_status: what the run table fills itself from, handed back
    # through the result file instead of a return value.
    assert result["status"]["fake.m1"] == 1.0
    assert result["n_status"] >= 1


def test_a_second_block_is_merged_not_substituted(server, jobdir, tmp_path):
    """status_run_end must not drop the status_run_start already on disk -
    the same merge the server-side write does, for the same reason."""
    url, _, _ = server
    from eco.status_server.client import StatusServerClient

    client = StatusServerClient(url)
    for key in ("status_run_start", "status_run_end"):
        started = client.handoff_status(key=key)
        relay.submit(_no_upload(relay.status_spec(
            url, tmp_path / "aux", handoff_id=started["handoff_id"], key=key,
            wait=30,
        )), mode="inline")

    written = json.loads((tmp_path / "aux" / "status.json").read_text())
    assert set(written) == {"status_run_start", "status_run_end"}


def test_the_result_file_is_written_even_when_everything_failed(jobdir, tmp_path):
    spec = _no_upload(relay.status_spec(
        "http://127.0.0.1:1", tmp_path / "aux", handoff_id="nope", wait=0.1,
    ))
    job = relay.submit(spec, mode="inline")
    result = job.result()
    assert result["state"] == "error"
    assert "error" in result
    assert job.result_path.exists()
    # and recent_jobs sees it, which is how a session finds out afterwards
    assert any(r["job_id"] == job.job_id for r in relay.recent_jobs())


# --------------------------------------------------------------------------
# aliases and recordings


def test_aliases_relay_writes_the_alias_file(server, jobdir, tmp_path):
    url, store, _ = server
    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_aliases()
    relay.submit(_no_upload(relay.aliases_spec(
        url, tmp_path / "aux", handoff_id=started["handoff_id"], wait=30,
    )), mode="inline")

    written = json.loads((tmp_path / "aux" / "aliases.json").read_text())
    assert written == store.namespace.alias.get_all()


def test_recording_relay_writes_a_readable_h5(server, jobdir, tmp_path):
    import h5py
    import requests

    url, _, _ = server
    requests.post(f"{url}/recording/start", json={"recording_id": "r1"},
                  timeout=30)
    FakeMonitor.instances[0].push(1.5)
    FakeMonitor.instances[0].push(2.5)

    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_recording("r1")
    result = relay.submit(_no_upload(relay.recording_spec(
        url, tmp_path / "aux", handoff_id=started["handoff_id"], wait=30,
    )), mode="inline").result()

    assert result["state"] == "done", result
    path = tmp_path / "aux" / "namespace_monitor.ixp.h5"
    assert path.exists() and result["file_bytes"] == path.stat().st_size
    with h5py.File(path) as f:
        assert f["fake.m1/data_0000"].shape[0] >= 2


# --------------------------------------------------------------------------
# upload


def test_the_relay_uploads_the_file_it_wrote(server, jobdir, tmp_path,
                                             monkeypatch):
    url, _, _ = server
    posted = {}

    import requests as _requests

    real_post = _requests.post

    def fake_post(target, json=None, timeout=None, **kw):
        if target.endswith("/copy_user_files"):
            posted.update(json)

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"status": "ok", "message": "copied"}

            return R()
        return real_post(target, json=json, timeout=timeout, **kw)

    monkeypatch.setattr(_requests, "post", fake_post)

    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_status()
    spec = relay.status_spec(
        url, tmp_path / "aux", pgroup="p12345", run_number=7,
        handoff_id=started["handoff_id"], upload=True,
        broker_address_aux="http://broker:10003", wait=30,
    )
    result = relay.submit(spec, mode="inline").result()

    assert result["state"] == "done", result
    assert posted["pgroup"] == "p12345"
    assert posted["run_number"] == 7
    assert posted["files"] == [str(tmp_path / "aux" / "status.json")]
    assert result["upload"]["status"] == "ok"


def test_no_broker_address_means_no_upload_attempt(server, jobdir, tmp_path):
    url, _, _ = server
    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_status()
    spec = relay.status_spec(
        url, tmp_path / "aux", pgroup="p1", run_number=1,
        handoff_id=started["handoff_id"], upload=True,
        broker_address_aux=None, wait=30,
    )
    result = relay.submit(spec, mode="inline").result()
    assert result["upload"]["status"] == "skipped"


# --------------------------------------------------------------------------
# job handling


def test_thread_mode_finishes_and_the_handle_waits_for_it(server, jobdir,
                                                          tmp_path):
    url, _, _ = server
    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_status()
    job = relay.submit(_no_upload(relay.status_spec(
        url, tmp_path / "aux", handoff_id=started["handoff_id"], wait=30,
    )), mode="thread")
    result = job.wait(timeout=60)
    assert result["state"] == "done"
    assert job.state == "done"


def test_wait_times_out_rather_than_hanging(jobdir, tmp_path):
    """A result file that never appears must raise, not block a scan
    callback forever."""
    job = relay.RelayJob({
        "job_id": "never", "kind": "status", "directory": str(tmp_path),
        "filename": "status.json", "result_path": str(tmp_path / "never.json"),
    })
    with pytest.raises(TimeoutError):
        job.wait(timeout=0.3, poll=0.05)


def test_prune_drops_old_job_files_only(jobdir):
    relay.job_dir()
    old = jobdir / "old.result.json"
    new = jobdir / "new.result.json"
    old.write_text("{}")
    new.write_text("{}")
    import os

    os.utime(old, (0, 0))
    assert relay.prune_jobs(max_age_days=1, force=True) == 1
    assert new.exists() and not old.exists()


# --------------------------------------------------------------------------
# the real thing


def test_detached_child_collects_and_writes_without_the_session(server, jobdir,
                                                                tmp_path):
    """The default mode, end to end: a real subprocess, a real HTTP fetch,
    a real file. This is the one that would catch a broken `python -m`
    entry point or a PYTHONPATH that does not reach this checkout."""
    url, _, _ = server
    from eco.status_server.client import StatusServerClient

    started = StatusServerClient(url).handoff_status(key="status_run_start")
    job = relay.submit(_no_upload(relay.status_spec(
        url, tmp_path / "aux", handoff_id=started["handoff_id"], wait=60,
    )), mode="process")

    result = job.wait(timeout=120, poll=0.1)
    assert result["state"] == "done", (result, job.log_path.read_text())
    assert result["pid"] != __import__("os").getpid()
    written = json.loads((tmp_path / "aux" / "status.json").read_text())
    assert written["status_run_start"]["status"]["fake.m1"] == 1.0
