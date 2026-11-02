"""eco.logs_elog -- the classic PSI elog (elog-gfa.psi.ch) for %ei.

The machine's logbooks are still kept there, SwissFEL commissioning above
all (shift handovers, beam drops, what the operators changed and why), and
some of Bernina's older history. This module reads them into the %ei
index, one item per entry, with the entry's own address for the citation.

Reading: elog's XML export (`<logbook>/?mode=XML`, `&last=N` for the last N
days). The first update reads a logbook completely -- SwissFEL
commissioning is ~42 000 entries, ~110 MB, about half a minute including
the parsing -- and each later one only the days since the previous update
(plus a day, so an entry edited shortly after it was written comes again).
Older entries edited later keep their indexed version until `--rebuild`.

Both default logbooks are readable without logging in. A logbook that asks
for a login is read as the beamline account (gac-bernina) with the elog
password eco's elog client uses (gopass `elog/elog-password`, or
~/.elog_psi), whoever runs the session.

TLS: elog-gfa sends its certificate without the intermediate one (DigiCert
EV RSA CA G2), so a verifying client fails with "unable to get local issuer
certificate" (browsers fetch the missing one themselves). ca_bundle() does
what the browsers do: it downloads the intermediate from the address in the
server's certificate and accepts it only if a root certifi trusts has
signed it. Verification stays on.
"""

import email.utils
import html
import math
import os
import re
import time
import warnings
from pathlib import Path
from urllib.parse import quote

from eco import logs_ask, logs_sources

URL = logs_ask.ELOG_WEB
DEFAULT_LOGBOOKS = ("SwissFEL commissioning", "SwissFEL Diagnostics")
USER = "gac-bernina"  # the account the stored elog password belongs to
CA_DIR = Path.home() / ".eco" / "ca"
FULL_AFTER_DAYS = 300  # longer ago than this: read the logbook completely

# elog's own fields; every other non-empty one is an attribute of the entry
# (Category, System, Domain, ...) and goes into the item's context
_META_FIELDS = {
    "MID", "DATE", "ENCODING", "TEXT", "Title", "Subject", "Author", "When", "Valid_until",
    "ATTACHMENT", "REPLY_TO", "IN_REPLY_TO", "LOCKED_BY", "DRAFT", "Eintrag-ID", "Datum",
}
_ENTRY_RX = re.compile(r"<ENTRY>(.*?)</ENTRY>", re.S)
_FIELD_RX = re.compile(r"<([A-Za-z_][\w.-]*)>(.*?)</\1>", re.S)
_LOGIN_RX = re.compile(r"name=\"?(uname|upassword)\b", re.I)


class LoginError(RuntimeError):
    pass


# -- reading ------------------------------------------------------------------------


def web_name(logbook):
    """"SwissFEL commissioning" as in the logbook's address: SwissFEL+commissioning."""
    return quote(logbook.replace(" ", "+"), safe="+")


def _password():
    """The elog password eco's elog client uses (eco.utilities.elog)."""
    try:
        from eco.utilities.secrets_gopass import get_gopass_password

        pw = get_gopass_password("elog/elog-password")
        if pw:
            return pw
    except Exception:
        pass
    try:
        return (Path.home() / ".elog_psi").read_text().strip() or None
    except OSError:
        return None


class Reader:
    """Read access to one elog server, anonymous until a logbook asks for a
    login."""

    def __init__(self, url=URL, session=None, timeout=120, user=USER):
        import requests

        self.url = url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.user = user
        self.identity = "anonymous"
        host = re.sub(r"^\w+://", "", self.url).split("/")[0]
        cached = _bundle_path(host)
        self._host = host
        self._verify = str(cached) if cached.exists() else True

    def _get(self, path, params):
        import requests

        try:
            return self.session.get(f"{self.url}/{path}", params=params, timeout=self.timeout, verify=self._verify)
        except requests.exceptions.SSLError as exc:
            if self._verify is not True or "local issuer" not in str(exc):
                raise
            self._verify = ca_bundle(self._host, timeout=min(self.timeout, 20))
            return self.session.get(f"{self.url}/{path}", params=params, timeout=self.timeout, verify=self._verify)

    def export(self, logbook, days=None):
        """The logbook's XML export (the last `days` days, or everything), as text."""
        params = {"mode": "XML"}
        if days:
            params["last"] = str(int(days))
        path = web_name(logbook) + "/"
        r = self._get(path, params)
        r.raise_for_status()
        text = _decode(r.content)
        if "<ELOG_LIST" not in text[:500] and _LOGIN_RX.search(text):
            self._login()
            r = self._get(path, params)
            r.raise_for_status()
            text = _decode(r.content)
            if "<ELOG_LIST" not in text[:500]:
                raise LoginError(f"elog {logbook!r}: the login as {self.user} was refused")
        return text

    def _login(self):
        password = _password()
        if not password:
            raise LoginError(
                "the elog asks for a login, and the elog password is not readable here "
                "(gopass elog/elog-password, or ~/.elog_psi)"
            )
        with warnings.catch_warnings():
            # passlib imports the stdlib `crypt` (deprecated) behind a try/except
            warnings.filterwarnings("ignore", message="'crypt' is deprecated", category=DeprecationWarning)
            from passlib.hash import sha256_crypt

        # what the elog package (and the elog web form) sends: sha256-crypt, no salt
        hashed = sha256_crypt.using(salt="", rounds=5000).hash(password)[4:]
        host = self._host.split(":")[0]
        self.session.cookies.set("unm", self.user, domain=host)
        self.session.cookies.set("upwd", hashed, domain=host)
        self.identity = f"the beamline account {self.user}"

    def entries(self, logbook, days=None):
        return parse_export(self.export(logbook, days=days))


def _decode(raw):
    """Exports mix UTF-8 with stray Latin-1 bytes (old entries): UTF-8
    where it is valid, Latin-1 for the rest."""
    text = raw.decode("utf-8", "surrogateescape")
    return re.sub(r"[\udc80-\udcff]", lambda m: chr(ord(m.group()) - 0xDC00), text)


def parse_export(text):
    """Entries of an XML export as dicts (mid, t, author, title, attrs, text).
    Matched field by field rather than with an XML parser: one malformed
    byte in 100 MB would lose the whole logbook."""
    out = []
    for body in _ENTRY_RX.findall(text):
        fields = {k: html.unescape(v) for k, v in _FIELD_RX.findall(body)}
        record = _record(fields)
        if record:
            out.append(record)
    return out


def _record(f):
    try:
        mid = int(f.get("MID", ""))
        t = email.utils.parsedate_to_datetime(f["DATE"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return None
    body = f.get("TEXT") or ""
    if (f.get("ENCODING") or "").lower() == "html":
        body = logs_sources.html_to_text(body)
    body = re.sub(r"[ \t]{3,}", "  ", body)
    body = re.sub(r"\n\s*\n\s*", "\n\n", body).strip()
    attrs = [v.strip() for k, v in f.items() if k not in _META_FIELDS and v.strip() and v.strip() != "- [0]"]
    return {
        "mid": mid,
        "t": t,
        "author": (f.get("Author") or "").strip(),
        "title": (f.get("Title") or f.get("Subject") or "").strip(),
        "attrs": attrs,
        "text": body,
    }


# -- the missing intermediate certificate -------------------------------------------


def _bundle_path(host):
    return CA_DIR / f"{host.replace(':', '_')}.pem"


def ca_bundle(host, port=443, timeout=20):
    """Path of a CA bundle for a server that does not send its intermediate
    certificate: certifi's roots plus that intermediate, downloaded from
    the issuer address in the server's certificate and kept only if a
    certifi root signed it and it signed the server's certificate. Cached
    in ~/.eco/ca/<host>.pem."""
    import ssl

    import certifi
    import requests
    from cryptography import x509
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    name = host.split(":")[0]
    if ":" in host:
        port = int(host.split(":")[1])
    # read only to find the issuer's address: nothing is trusted from it
    leaf = x509.load_pem_x509_certificate(ssl.get_server_certificate((name, port), timeout=timeout).encode())
    aia = leaf.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    urls = [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]
    if not urls:
        raise ssl.SSLError(f"{host}: certificate names no issuer to download")
    content = requests.get(urls[0], timeout=timeout).content
    try:
        inter = x509.load_der_x509_certificate(content)
    except ValueError:
        inter = x509.load_pem_x509_certificate(content)
    roots_pem = Path(certifi.where()).read_bytes()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a few old roots in certifi have a non-positive serial
        roots = [r for r in x509.load_pem_x509_certificates(roots_pem) if r.subject == inter.issuer]
    if not _issued_by(inter, roots) or not _issued_by(leaf, [inter]) or not _is_ca(inter):
        raise ssl.SSLError(f"{host}: the downloaded issuer certificate is not signed by a trusted root")
    from cryptography.hazmat.primitives.serialization import Encoding

    path = _bundle_path(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(roots_pem + b"\n" + inter.public_bytes(Encoding.PEM))
    os.replace(tmp, path)
    return str(path)


def _issued_by(cert, issuers):
    for issuer in issuers:
        try:
            cert.verify_directly_issued_by(issuer)
            return True
        except Exception:
            continue
    return False


def _is_ca(cert):
    from cryptography import x509

    try:
        return cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return False


# -- indexing -----------------------------------------------------------------------


def update(logbooks=DEFAULT_LOGBOOKS, reader=None, db_path=logs_ask.DEFAULT_DB, report=print, rebuild=False):
    """Index the entries of `logbooks` written since the last update (all of
    them the first time, or with rebuild), and drop those of logbooks no
    longer in the list. Returns {"logbooks", "new_entries", "items",
    "dropped", "identity"}."""
    reader = reader or Reader()
    logbooks = list(logbooks)
    con = logs_ask._connect(db_path)
    try:
        dropped = _forget(con, keep=set(logbooks))
        if rebuild:
            _forget(con, drop=set(logbooks))
        last = dict(con.execute("SELECT key, value FROM meta WHERE key LIKE 'elog_last:%'"))
        new = 0
        for logbook in logbooks:
            since = last.get(f"elog_last:{logbook}")
            days = math.ceil((time.time() - float(since)) / 86400) + 1 if since else None
            if days and days > FULL_AFTER_DAYS:
                days = None
            started = time.time()
            if report and days is None:
                report(f"  {logbook}: reading all entries ...")
            entries = reader.entries(logbook, days=days)
            with con:
                if days is None:
                    _forget(con, drop={logbook})
                else:
                    _forget_entries(con, logbook, {e["mid"] for e in entries})
                for e in entries:
                    context = f"{e['author']} [{logbook}]" + (f" [{', '.join(e['attrs'])}]" if e["attrs"] else "")
                    text = (e["title"] + "\n" + e["text"]).strip()
                    con.execute(
                        "INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                        (text, context, "elog", "ok", "", e["t"], f"elog {logbook}", f"elog:{logbook}:{e['mid']}"),
                    )
                con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"elog_last:{logbook}", str(started)))
            new += len(entries)
            if report:
                report(f"  {logbook}: {len(entries)} {'entries' if days is None else f'entries of the last {days} days'}")
        total = con.execute("SELECT count(*) FROM items").fetchone()[0]
    finally:
        con.close()
    return {"logbooks": len(logbooks), "new_entries": new, "items": total, "dropped": dropped, "identity": reader.identity}


def _forget(con, keep=None, drop=None):
    """Drop the entries of logbooks not in `keep`, or of those in `drop`,
    with where their fetch stood. Returns how many entries went."""
    rows = con.execute("SELECT rowid, path FROM items WHERE kind = 'elog'")
    stale, gone = [], set()
    for rowid, path in rows:
        logbook = elog_ids(path)[0]
        if (keep is not None and logbook not in keep) or (drop is not None and logbook in drop):
            stale.append((rowid,))
            gone.add(logbook)
    keys = [k for (k,) in con.execute("SELECT key FROM meta WHERE key LIKE 'elog_last:%'")]
    forget = [
        (k,) for k in keys
        if (keep is not None and k.split(":", 1)[1] not in keep) or (drop is not None and k.split(":", 1)[1] in drop)
    ]
    with con:
        con.executemany("DELETE FROM items WHERE rowid = ?", stale)
        con.executemany("DELETE FROM meta WHERE key = ?", forget)
    return len(stale)


def _forget_entries(con, logbook, mids):
    """Drop indexed copies of entries fetched again: one scan, then by rowid."""
    if not mids:
        return
    rows = con.execute("SELECT rowid, path FROM items WHERE kind = 'elog'")
    stale = []
    for rowid, path in rows:
        lb, mid = elog_ids(path)
        if lb == logbook and mid in mids:
            stale.append((rowid,))
    con.executemany("DELETE FROM items WHERE rowid = ?", stale)


elog_ids = logs_ask.elog_ids


def indexed_logbooks(db_path=logs_ask.DEFAULT_DB):
    """{logbook: time of its last update} of the logbooks indexed so far."""
    con = logs_ask._connect(db_path)
    rows = con.execute("SELECT key, value FROM meta WHERE key LIKE 'elog_last:%'").fetchall()
    con.close()
    return {key.split(":", 1)[1]: float(value) for key, value in rows}
