"""eco.scilog_app: the SciLog viewer is the scilog-viewer package, started with
eco's parts (eco.scilog_viewer_plugin). The viewer itself is tested in its own
repository."""

import socket
import urllib.request

import pytest

pytest.importorskip("scilog_viewer")
pytest.importorskip("tornado")

from _light_eco import ensure_eco_importable  # noqa: E402

ensure_eco_importable()


def test_the_plugin_adds_the_beamline_account_and_eco_settings(monkeypatch):
    from scilog_viewer import client, load_plugin, server

    from eco import logs_scilog

    for name in ("FUNCTIONAL_USER", "SHARED_ACCOUNTS", "functional_login", "TOKEN_PATH", "TOKEN_HELP"):
        monkeypatch.setattr(client, name, getattr(client, name))
    for name in ("CONFIG_PATH", "DEFAULT_WRITE_LOGBOOKS"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(client, "_functional", {})
    load_plugin("eco.scilog_viewer_plugin")
    assert client.FUNCTIONAL_USER == logs_scilog.FUNCTIONAL_USER
    assert "gac-bernina" in client.SHARED_ACCOUNTS
    assert client.TOKEN_PATH == logs_scilog.TOKEN_PATH
    assert server.CONFIG_PATH.name == "scilog_app.json" and server.DEFAULT_WRITE_LOGBOOKS == ["68afe9fe6f90f96e7370f9b3"]

    # the beamline account logs in with eco's password, once until its token runs out
    logins = []
    monkeypatch.setattr(logs_scilog, "_functional_password", lambda: "pw")
    monkeypatch.setattr(client, "login", lambda user, pw, **kw: logins.append(user) or "a.eyJleHAiOiA5OTk5OTk5OTk5fQ.s")
    monkeypatch.setattr("getpass.getuser", lambda: "gac-bernina")
    assert client.functional_available()
    assert client.reader().identity == f"the beamline account {logs_scilog.FUNCTIONAL_USER}"
    client.reader()
    assert logins == [logs_scilog.FUNCTIONAL_USER]
    monkeypatch.setattr(logs_scilog, "_functional_password", lambda: None)
    assert not client.functional_available()


def test_start_runs_the_package_with_the_plugin(monkeypatch):
    import scilog_viewer

    from eco import scilog_app

    calls = []
    monkeypatch.setattr(scilog_viewer, "start", lambda **kw: calls.append(kw) or "http://x")
    assert scilog_app.start(logbook="a" * 24, open_browser=False) == "http://x"
    assert calls[0]["plugin"] == "eco.scilog_viewer_plugin" and calls[0]["logbook"] == "a" * 24
    assert (calls[0]["path"][0] / "eco" / "scilog_app").is_dir()
    assert scilog_app.content is __import__("scilog_viewer.content").content  # the rest is the package's


def test_the_server_starts_and_serves_the_page():
    from eco import scilog_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = scilog_app.start(port=port, open_browser=False)
    try:
        import http.cookiejar

        # never through a proxy; the key becomes a cookie on the first visit
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(url, timeout=10) as r:
            assert r.status == 200 and b"SciLog" in r.read()
        assert "Traceback" not in scilog_app.log()
    finally:
        scilog_app.stop()
