"""eco.utilities.elog_scilog: an expired beamline-account token is replaced
by a fresh login instead of failing ("jwt expired"), with the real `scilog`
package and only the network faked."""

import json

import pytest

pytest.importorskip("scilog")
pytest.importorskip("markdown")


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.reason = "OK" if self.ok else "Unauthorized"
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        import requests

        if not self.ok:
            return requests.HTTPError(f"{self.status_code} Client Error: {self.reason}", response=self)


def test_an_expired_token_logs_in_again(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # the package's token file, holding a token that has run out
    (tmp_path / ".scilogrestapi-tokens").write_text(json.dumps({"swissfelaramis-bernina@psi.ch": "Bearer old"}))
    import scilog.httpclient as hc
    from eco.utilities import elog_scilog

    calls = []

    def get(url, params=None, headers=None, timeout=None, verify=None):
        calls.append(("get", headers["Authorization"]))
        if headers["Authorization"] == "Bearer old":
            return _Response(401, {"error": {"statusCode": 401, "message": "Error verifying token : jwt expired"}})
        return _Response(200, [{"id": "64ee4bd69e8ab0fb8ecdbb6d", "name": "p23618", "ownerGroup": "p23618",
                                "snippetType": "logbook", "createdBy": "scilog-admin@psi.ch"}])

    def post(url, json=None, headers=None, timeout=None, verify=None):
        calls.append(("login", json["principal"]))
        return _Response(200, {"token": "fresh"})

    monkeypatch.setattr(hc.requests, "get", get)
    monkeypatch.setattr(hc.requests, "post", post)

    log, user = elog_scilog.getDefaultElogInstance(pgroup="p23618", password="pw")
    assert [c[0] for c in calls] == ["get", "login", "get"]
    assert calls[-1] == ("get", "Bearer fresh") and log.logbook.id == "64ee4bd69e8ab0fb8ecdbb6d"

    # anything other than an authorization failure is not retried
    monkeypatch.setattr(hc.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network down")))
    with pytest.raises(ConnectionError):
        elog_scilog._again_if_expired(log, log.get_logbooks, ownerGroup="p23618")
