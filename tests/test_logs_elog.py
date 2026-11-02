"""eco.logs_elog: the elog-gfa XML export into the %ei index, against a
fake server; the completed certificate chain against generated certificates."""

import datetime
import json
import time

import pytest

import eco.ei as ei
import eco.logs_ask as la
import eco.logs_elog as le


def _entry(mid, date, title, text, author="Kittel", encoding="HTML", **attrs):
    fields = "".join(f"<{k}>{v}</{k}>" for k, v in attrs.items())
    return (
        f"<ENTRY><MID>{mid}</MID><DATE>{date}</DATE><ENCODING>{encoding}</ENCODING>"
        f"<Author>{author}</Author>{fields}<Valid_until>- [0]</Valid_until>"
        f"<Title>{title}</Title><TEXT>{text}</TEXT></ENTRY>"
    )


class _Response:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Cookies(dict):
    def set(self, name, value, domain=None):
        self[name] = value


class _Server:
    """Answers <logbook>/?mode=XML[&last=N]; logbooks in `private` need the login cookies."""

    def __init__(self):
        self.logbooks = {
            "SwissFEL+commissioning": [
                (101, "Sun, 01 Nov 2026 13:48:00 +0100", "Snapshot broken", "&lt;p&gt;Save button fails&lt;/p&gt;", {"Category": "Problem", "System": "Controls"}),
                (102, "Mon, 02 Nov 2026 08:02:00 +0100", "Athos beam drops", "&lt;p&gt;MPS link&lt;/p&gt;&lt;style&gt;td {x}&lt;/style&gt;", {"Category": "Problem", "Section": ""}),
            ],
            "Private+book": [(7, "Mon, 02 Nov 2026 09:00:00 +0100", "secret", "plain text", {})],
        }
        self.private = {"Private+book"}
        self.requests = []
        self.cookies = _Cookies()

    def get(self, url, params=None, timeout=None, verify=None):
        logbook = url.split("elog-gfa.psi.ch/", 1)[1].rstrip("/")
        self.requests.append((logbook, dict(params)))
        if logbook in self.private and self.cookies.get("unm") != "gac-bernina":
            return _Response(b'<html><form><input type="text" name="uname"><input type=password name="upassword"></form></html>')
        rows = self.logbooks[logbook]
        if "last" in params:
            rows = rows[-1:]
        body = "".join(_entry(m, d, t, x, **a) for m, d, t, x, a in rows)
        # an old entry's stray Latin-1 byte: the rest must still be read
        return _Response(("<?xml version='1.0'?><ELOG_LIST>" + body + "</ELOG_LIST>").encode().replace(b"Kittel", b"Kitt\xe9l", 1))


def _reader(server):
    r = le.Reader(session=server)
    r._verify = True
    return r


def test_entries_are_indexed_with_their_address_and_updated_incrementally(tmp_path):
    db, server = tmp_path / "i.sqlite", _Server()
    r = le.update(["SwissFEL commissioning"], reader=_reader(server), db_path=db, report=None)
    assert r["new_entries"] == 2 and server.requests[-1][1] == {"mode": "XML"}
    hit = la.search("Athos beam drops", k=1, db_path=db, kinds=("elog",))[0]
    assert hit.text == "Athos beam drops\nMPS link"  # no style sheet text
    assert hit.context == "Kittel [SwissFEL commissioning] [Problem]"
    assert hit.cite() == "[elog " + time.strftime("%Y-%m-%d %H:%M", time.localtime(hit.t)) + " Kittel [SwissFEL commissioning] [Problem]]"
    assert hit.location() == "https://elog-gfa.psi.ch/SwissFEL+commissioning/102"
    first = la.search("Snapshot", k=1, db_path=db, kinds=("elog",))[0]
    assert first.context.startswith("Kittél")  # Latin-1 byte decoded, entry kept
    context = la.item_context(first, db_path=db)
    assert "> " in context and "Save button fails" in context and "Athos beam drops" in context

    # later: only the last days, the entry fetched again replaces its copy
    r = le.update(["SwissFEL commissioning"], reader=_reader(server), db_path=db, report=None)
    assert server.requests[-1][1] == {"mode": "XML", "last": "2"}  # the days since, plus one
    assert r["items"] == 2

    # a logbook taken out of the list is dropped
    r = le.update([], reader=_reader(server), db_path=db, report=None)
    assert r["dropped"] == 2 and r["items"] == 0 and le.indexed_logbooks(db) == {}


def test_a_logbook_asking_for_a_login_is_read_as_the_beamline_account(tmp_path, monkeypatch):
    db, server = tmp_path / "i.sqlite", _Server()
    monkeypatch.setattr(le, "_password", lambda: "pw")
    r = le.update(["Private book"], reader=_reader(server), db_path=db, report=None)
    assert r["new_entries"] == 1 and r["identity"] == "the beamline account gac-bernina"
    assert server.cookies["upwd"] and server.cookies["upwd"] != "pw"  # sha256-crypt, as elog expects

    monkeypatch.setattr(le, "_password", lambda: None)
    with pytest.raises(le.LoginError, match="elog-password"):
        _reader(_Server()).entries("Private book")


def test_time_spans_in_questions():
    now = time.mktime((2026, 11, 2, 16, 0, 0, 0, 0, -1))  # Monday 16:00
    at = lambda *t: time.mktime((2026, *t, 0, 0, 0, -1))
    assert ei.time_window("what happened over the last day?", now) == (now - 86400, None)
    assert ei.time_window("what happened over the last day or so?", now) == (now - 1.5 * 86400, None)
    assert ei.time_window("beam problems in the past 3 hours", now) == (now - 3 * 3600, None)
    assert ei.time_window("what happened yesterday", now) == (at(11, 1, 0, 0), at(11, 2, 0, 0))
    assert ei.time_window("anything since yesterday", now) == (at(11, 1, 0, 0), None)
    assert ei.time_window("Athos problems since Friday?", now) == (at(10, 30, 0, 0), None)
    assert ei.time_window("and last night?", now) == (at(11, 1, 18, 0), None)
    assert ei.time_window("how do I set the attenuator transmission?", now) is None


def test_a_time_question_gets_the_entries_of_that_span_in_order(tmp_path):
    db, server = tmp_path / "i.sqlite", _Server()
    le.update(["SwissFEL commissioning"], reader=_reader(server), db_path=db, report=None)
    cfg = json.loads(json.dumps(ei.DEFAULT_CONFIG))
    cfg.update(scilog_default=None, elog_logbooks=None)
    a = ei.Assistant(config=cfg, db_path=db)
    now = time.mktime((2026, 11, 2, 16, 0, 0, 0, 0, -1))
    hits = a.recent_hits("what happened to the machine since yesterday?", now=now)
    assert [h.path for h in hits] == ["elog:SwissFEL commissioning:101", "elog:SwissFEL commissioning:102"]
    assert a.recent_hits("how do I scan the mono?", now=now) == []
    assert "Now: " in a.messages("anything new?", hits)[-1]["content"]
    # cited to the minute
    ans = ei.Answer("q", "drops at [elog " + time.strftime("%Y-%m-%d %H:%M", time.localtime(hits[1].t)) + "]", hits, "m", 1.0)
    assert ans.cited == [hits[1]]


# -- the missing intermediate certificate ----------------------------------------------


def _ca(name, issuer=None, issuer_key=None, ca=True, aia=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    b = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if aia:
        b = b.add_extension(x509.AuthorityInformationAccess(
            [x509.AccessDescription(AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier(aia))]
        ), critical=False)
    return b.sign(issuer_key or key, hashes.SHA256()), key


def test_the_intermediate_is_kept_only_when_a_trusted_root_signed_it(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    import ssl

    import certifi
    import requests
    from cryptography.hazmat.primitives.serialization import Encoding

    root, root_key = _ca("Root")
    inter, inter_key = _ca("Intermediate", root, root_key)
    leaf, _ = _ca("elog-gfa.psi.ch", inter, inter_key, ca=False, aia="http://ca.example/inter.crt")
    fake_root, fake_root_key = _ca("Root")  # same name, not trusted
    forged, forged_key = _ca("Intermediate", fake_root, fake_root_key)
    forged_leaf, _ = _ca("elog-gfa.psi.ch", forged, forged_key, ca=False, aia="http://ca.example/inter.crt")

    roots = tmp_path / "roots.pem"
    roots.write_bytes(root.public_bytes(Encoding.PEM))
    monkeypatch.setattr(certifi, "where", lambda: str(roots))
    monkeypatch.setattr(le, "CA_DIR", tmp_path / "ca")
    served = {}
    monkeypatch.setattr(ssl, "get_server_certificate", lambda addr, timeout=None: served["leaf"].public_bytes(Encoding.PEM).decode())
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _Response(served["inter"].public_bytes(Encoding.DER)))

    served.update(leaf=forged_leaf, inter=forged)
    with pytest.raises(ssl.SSLError, match="not signed by a trusted root"):
        le.ca_bundle("elog-gfa.psi.ch")
    assert not (tmp_path / "ca").exists()

    served.update(leaf=leaf, inter=inter)
    path = le.ca_bundle("elog-gfa.psi.ch")
    assert open(path, "rb").read().count(b"BEGIN CERTIFICATE") == 2
    assert le.Reader()._verify == path  # used from then on
