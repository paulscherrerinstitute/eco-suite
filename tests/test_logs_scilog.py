"""eco.logs_scilog: SciLog read with a personal token, against a fake
server that answers the few LoopBack filters the module sends."""

import base64
import json
import re
import sqlite3
import stat
import time
import types

import eco.ei as ei
import eco.logs_ask as la
import eco.logs_scilog as ls


def _jwt(exp_in=86400, **claims):
    body = {"name": "Ann Example", "email": "ann@psi.ch", "roles": ["p12345", "p23456"], "exp": time.time() + exp_in, **claims}
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc(body)}.signature"


TOKEN = _jwt()


class _Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _post(i, lb, created, text, deleted=False, kind="paragraph"):
    return {
        "id": f"s{i}", "parentId": lb, "createdAt": created, "createdBy": "ann@psi.ch", "tags": ["t"] if i % 2 else [],
        "textcontent": f"<p>{text}</p>", "snippetType": kind, "deleted": deleted,
    }


class _Server:
    """GET only; counts requests per path."""

    def __init__(self):
        self.logbooks = [
            {"id": "lb1", "name": "26j_kern_p23618", "ownerGroup": "", "location": "loc1", "touchedAt": "2026-10-30T00:00:00Z"},
            {"id": "lb2", "name": "Bernina instrument log", "ownerGroup": "", "location": "loc1", "touchedAt": "2026-10-29T00:00:00Z"},
            {"id": "lb3", "name": "alvra thing", "ownerGroup": "p11111", "location": "loc2", "touchedAt": "2026-10-28T00:00:00Z"},
            {"id": "lb4", "name": "gone", "ownerGroup": "p1", "location": "loc1", "deleted": True},
        ]
        self.locations = [{"id": "loc1", "name": "ARAMIS-BERNINA"}, {"id": "loc2", "name": "ARAMIS-ALVRA"}]
        self.snippets = [
            _post(1, "lb1", "2026-10-13T10:00:00.000Z", "Did the timing of the freespace laser"),
            _post(2, "lb1", "2026-10-13T11:00:00.000Z", "transmission check</p><p>att.set_transmission(0.1)"),
            _post(3, "lb2", "2026-10-14T09:00:00.000Z", "USD vented"),
            _post(4, "lb2", "2026-10-14T10:00:00.000Z", "deleted post", deleted=True),
            _post(5, "lb2", "2026-10-14T11:00:00.000Z", "a task", kind="task"),
            _post(6, "lb3", "2026-10-15T09:00:00.000Z", "alvra laser"),
        ]
        self.requests = []
        self.token = TOKEN
        self.password = "beamline-pw"

    def post(self, url, json=None, timeout=None):
        self.requests.append("POST " + url.split("/api/v1", 1)[1])
        if json != {"principal": ls.FUNCTIONAL_USER, "password": self.password}:
            return _Response(401, {"error": "Unauthorized"})
        return _Response(200, {"token": self.token})

    def get(self, url, params=None, timeout=None, headers=None):
        path = url.split("/api/v1", 1)[1]
        self.requests.append(path)
        if headers.get("Authorization") != "Bearer " + self.token:
            return _Response(401, {"error": "Unauthorized"})
        filt = json.loads(params["filter"]) if params else {}
        if path == "/users/me":
            return _Response(200, {"id": "u1", "email": "ann@psi.ch"})
        if path == "/logbooks":
            return _Response(200, self.logbooks)
        if path == "/locations":
            return _Response(200, self.locations)
        rows = self.snippets
        if path.startswith("/basesnippets/search="):
            from urllib.parse import unquote

            rx = re.compile(unquote(path.split("=", 1)[1]), re.I)
            rows = [r for r in rows if rx.search(r["textcontent"])]
        elif path != "/basesnippets":
            return _Response(404, {})
        where = filt.get("where", {})
        rows = [r for r in rows if r["snippetType"] == where.get("snippetType", r["snippetType"])]
        parent = where.get("parentId")
        if isinstance(parent, dict):
            rows = [r for r in rows if r["parentId"] in parent["inq"]]
        elif parent:
            rows = [r for r in rows if r["parentId"] == parent]
        if "createdAt" in where:
            rows = [r for r in rows if r["createdAt"] > where["createdAt"]["gt"]]
        rows = sorted(rows, key=lambda r: r["createdAt"], reverse=filt.get("order") == ["createdAt DESC"])
        skip = filt.get("skip", 0)
        return _Response(200, rows[skip:skip + filt.get("limit", 10**9)])


def _reader(server=None):
    server = server or _Server()
    return ls.Reader(TOKEN, url="https://scilog.example/api/v1", session=server), server


# -- the token ----------------------------------------------------------------------


def test_token_from_env_then_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "_shared_account", lambda: None)
    path = tmp_path / "scilog_token"
    monkeypatch.delenv("ECO_SCILOG_TOKEN", raising=False)
    assert ls.token(path) == (None, None)
    assert ls.save_token("Bearer " + TOKEN + "\n", path) == path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ls.token(path) == (TOKEN, str(path))
    monkeypatch.setenv("ECO_SCILOG_TOKEN", '"other.to.ken"')
    assert ls.token(path) == ("other.to.ken", "$ECO_SCILOG_TOKEN")


def test_save_token_rejects_what_is_not_a_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "_shared_account", lambda: None)
    for junk in ("", "my password", "a.b"):
        try:
            ls.save_token(junk, tmp_path / "t")
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {junk!r}")
    assert not (tmp_path / "t").exists()


def test_describe_reads_name_groups_and_expiry():
    assert ls.describe(TOKEN).startswith("Ann Example <ann@psi.ch>, 2 groups, valid until ")
    assert "EXPIRED since" in ls.describe(_jwt(exp_in=-60))
    assert ls.describe("garbage") == "?, 0 groups"


def test_no_token_and_a_refused_token_say_how_to_get_one(monkeypatch, tmp_path):
    monkeypatch.delenv("ECO_SCILOG_TOKEN", raising=False)
    monkeypatch.setattr(ls, "TOKEN_PATH", tmp_path / "missing")
    try:
        ls.Reader()
    except ls.TokenError as exc:
        assert "copy(localStorage.id_token)" in str(exc)
    else:
        raise AssertionError("no TokenError")
    reader, server = _reader()
    server.token = "a.different.one"
    try:
        reader.me()
    except ls.TokenError as exc:
        assert "refused" in str(exc) and "%ei --scilog-token new" in str(exc)
    else:
        raise AssertionError("no TokenError")


def _no_personal_token(monkeypatch, tmp_path):
    monkeypatch.delenv("ECO_SCILOG_TOKEN", raising=False)
    monkeypatch.setattr(ls, "TOKEN_PATH", tmp_path / "scilog_token")
    monkeypatch.setattr(ls, "_functional", {})


def test_without_a_personal_token_the_beamline_account_logs_in(tmp_path, monkeypatch):
    _no_personal_token(monkeypatch, tmp_path)
    monkeypatch.setattr(ls, "_shared_account", lambda: None)
    server = _Server()
    monkeypatch.setattr(ls, "_functional_password", lambda: "beamline-pw")
    r = ls.reader(url="https://x/api/v1", session=server)
    assert r.identity == f"the beamline account {ls.FUNCTIONAL_USER}" and r.token == TOKEN
    assert len(r.logbooks()) == 3
    ls.reader(url="https://x/api/v1", session=server)
    assert server.requests.count("POST /users/login") == 1  # the token is kept until it expires
    # a personal token wins on a personal account
    ls.save_token(_jwt(name="Ann"), ls.TOKEN_PATH)
    assert ls.reader(url="https://x/api/v1", session=server).identity.startswith("Ann <ann@psi.ch> (token from ")
    # no token and no password: how to get a token
    monkeypatch.setattr(ls, "_functional_password", lambda: None)
    ls.TOKEN_PATH.unlink()
    try:
        ls.reader(url="https://x/api/v1", session=server)
    except ls.TokenError as exc:
        assert "copy(localStorage.id_token)" in str(exc) and "scilog-password" in str(exc)
    else:
        raise AssertionError("no TokenError")


def test_a_shared_account_never_reads_or_stores_a_personal_token(tmp_path, monkeypatch):
    _no_personal_token(monkeypatch, tmp_path)
    monkeypatch.setattr(ls, "_shared_account", lambda: None)
    ls.save_token(_jwt(name="Ann"), ls.TOKEN_PATH)  # left there before
    monkeypatch.setattr(ls, "_shared_account", lambda: "gac-bernina")
    try:
        ls.save_token(TOKEN, tmp_path / "other")
    except ValueError as exc:
        assert "shared account" in str(exc)
    else:
        raise AssertionError("stored a personal token on a shared account")
    assert not (tmp_path / "other").exists()
    monkeypatch.setattr(ls, "_functional_password", lambda: "beamline-pw")
    r = ls.reader(url="https://x/api/v1", session=_Server())
    assert r.identity.startswith("the beamline account")


# -- reading ------------------------------------------------------------------------


def test_logbooks_filters_and_pgroup_from_the_name():
    reader, _ = _reader()
    lbs = reader.logbooks()
    assert [lb["id"] for lb in lbs] == ["lb1", "lb2", "lb3"]  # deleted one dropped, newest first
    assert lbs[0] == {
        "id": "lb1", "name": "26j_kern_p23618", "pgroup": "p23618", "location": "ARAMIS-BERNINA",
        "private": False, "touched": "2026-10-30T00:00:00Z",
    }
    assert [lb["id"] for lb in reader.logbooks(location="bernina")] == ["lb1", "lb2"]
    assert [lb["id"] for lb in reader.logbooks(pgroup="p2361")] == ["lb1"]
    assert [lb["id"] for lb in reader.logbooks(location="BERNINA", name="instrument")] == ["lb2"]


def test_paragraphs_pages_skip_deleted_and_tasks(monkeypatch):
    monkeypatch.setattr(ls, "PAGE", 1)
    reader, server = _reader()
    posts = reader.paragraphs("lb2")
    assert [(p["id"], p["text"], p["logbook"]) for p in posts] == [("s3", "USD vented", "lb2")]
    posts = reader.paragraphs(["lb1", "lb3"], after_iso="2026-10-13T10:00:00.000Z")
    assert [p["id"] for p in posts] == ["s2", "s6"]
    # a page per post (the deleted one too: dropped here, not by the server), plus the empty last page
    assert server.requests.count("/basesnippets") == 3 + 3


def test_search_with_several_words_in_any_order():
    reader, _ = _reader()
    assert [p["id"] for p in reader.search("laser")] == ["s6", "s1"]  # newest first
    assert [p["id"] for p in reader.search("laser freespace")] == ["s1"]
    assert reader.search("freespace nothing") == []


def test_parse_filters():
    assert ls.parse_filters("--location bernina --pgroup p23618") == {"location": "bernina", "pgroup": "p23618"}
    assert ls.parse_filters('instrument log --all --rebuild') == {"name": "instrument log", "all": True, "rebuild": True}
    assert ls.parse_filters("") == {}
    try:
        ls.parse_filters("--bogus x")
    except ValueError as exc:
        assert "--bogus" in str(exc)
    else:
        raise AssertionError("no ValueError")


# -- indexing -----------------------------------------------------------------------


def test_index_reads_new_logbooks_whole_and_the_rest_in_one_request(tmp_path):
    db = tmp_path / "idx.sqlite"
    reader, server = _reader()
    lbs = reader.logbooks(location="bernina")
    result = ls.index_logbooks(reader, lbs, db_path=db, report=None)
    assert result["new_posts"] == 3 and result["logbooks"] == 2
    hits = la.search("freespace laser", k=5, db_path=db, kinds=("scilog",))
    assert hits[0].context == "ann@psi.ch [26j_kern_p23618 (p23618)] [t]"
    assert la.search("transmission", k=5, db_path=db, kinds=("note_command",))[0].text == "att.set_transmission(0.1)"

    # nothing new: one request for both logbooks
    server.requests.clear()
    assert ls.index_logbooks(reader, lbs, db_path=db, report=None)["new_posts"] == 0
    assert server.requests == ["/basesnippets"]

    # a new post in a logbook that had none since: found by that request
    server.snippets.append(_post(7, "lb2", "2026-10-20T09:00:00.000Z", "new in the instrument log"))
    server.requests.clear()
    assert ls.index_logbooks(reader, lbs, db_path=db, report=None)["new_posts"] == 1
    assert server.requests == ["/basesnippets"]

    # a logbook added to the choice is read whole, the others still together
    everything = reader.logbooks()
    server.requests.clear()
    assert ls.index_logbooks(reader, everything, db_path=db, report=None)["new_posts"] == 1
    assert server.requests == ["/basesnippets", "/basesnippets"]
    con = sqlite3.connect(db)
    paths = [p for (p,) in con.execute("SELECT path FROM items WHERE kind = 'scilog'")]
    assert len(paths) == len(set(paths)) == 5


def test_a_post_indexed_through_the_functional_account_is_not_kept_twice(tmp_path):
    db = tmp_path / "idx.sqlite"
    con = la._connect(db)
    con.execute("INSERT INTO items VALUES ('old copy', 'x', 'scilog', 'ok', '', 0, 'scilog p23618', 'scilog:p23618:s1')")
    con.commit()
    con.close()
    reader, _ = _reader()
    ls.index_logbooks(reader, reader.logbooks(pgroup="p23618"), db_path=db, report=None)
    texts = [h.text for h in la.search("freespace OR copy", k=10, db_path=db, kinds=("scilog",))]
    assert "old copy" not in texts and any("freespace" in t for t in texts)


def test_logbooks_no_longer_readable_are_dropped(tmp_path):
    db = tmp_path / "idx.sqlite"
    con = la._connect(db)  # one post from the older per-pgroup fetch
    con.execute("INSERT INTO items VALUES ('old fetch', 'x', 'scilog', 'ok', '', 0, 'scilog p23618', 'scilog:p23618:s9')")
    con.commit()
    con.close()
    reader, server = _reader()
    r = ls.update(reader, {"all": True}, db_path=db, report=None)
    assert (r["new_posts"], r["dropped"]) == (4, 1)
    server.logbooks = [lb for lb in server.logbooks if lb["id"] != "lb1"]  # access to lb1 withdrawn
    r = ls.update(reader, {"all": True}, db_path=db, report=None)
    assert (r["new_posts"], r["dropped"]) == (0, 2)
    con = sqlite3.connect(db)
    assert sorted(p for (p,) in con.execute("SELECT path FROM items WHERE path LIKE 'scilog:%'")) == [
        "scilog:lb2:s3", "scilog:lb3:s6",
    ]
    assert [k for (k,) in con.execute("SELECT key FROM meta WHERE key LIKE 'scilog_logbook_last:%' ORDER BY key")] == [
        "scilog_logbook_last:lb2", "scilog_logbook_last:lb3",
    ]
    # a filter chooses among the readable ones only
    assert ls.update(reader, {"location": "bernina"}, db_path=db, report=None)["logbooks"] == 1
    # an edited and a deleted post show only after a rebuild
    server.snippets[2]["textcontent"] = "<p>USD vented, edited</p>"
    server.snippets[5]["deleted"] = True
    assert ls.update(reader, {"all": True}, db_path=db, report=None)["new_posts"] == 0
    assert ls.update(reader, {"all": True, "rebuild": True}, db_path=db, report=None)["new_posts"] == 1
    assert [t for (t,) in con.execute("SELECT text FROM items WHERE kind = 'scilog'")] == ["USD vented, edited"]


def test_a_post_links_to_its_logbook_and_opens_among_its_neighbours(tmp_path):
    db = tmp_path / "idx.sqlite"
    reader, _ = _reader()
    ls.update(reader, {"location": "bernina"}, db_path=db, report=None)
    hit = la.search("transmission check", k=1, db_path=db, kinds=("scilog",))[0]
    assert hit.location() == "https://scilog.psi.ch/logbooks/lb1/dashboard"
    text = la.item_context(hit, db_path=db)
    assert text.splitlines()[0].endswith("https://scilog.psi.ch/logbooks/lb1/dashboard")
    assert "  ann@psi.ch\nDid the timing" in text  # the post before, short
    assert "> " in text and "att.set_transmission(0.1)" in text
    cmd = la.search("transmission", k=1, db_path=db, kinds=("note_command",))[0]
    assert cmd.location() == hit.location()


def test_selection_is_remembered(tmp_path):
    db = tmp_path / "idx.sqlite"
    assert ls.load_selection(db) is None
    ls.save_selection({"location": "bernina"}, db)
    assert ls.load_selection(db) == {"location": "bernina"}


# -- %ei ------------------------------------------------------------------------------


def test_the_magic_commands(tmp_path, monkeypatch, capsys):
    server = _Server()
    monkeypatch.setattr(ls, "TOKEN_PATH", tmp_path / "scilog_token")
    monkeypatch.delenv("ECO_SCILOG_TOKEN", raising=False)
    monkeypatch.setattr(ls, "_shared_account", lambda: None)
    real_reader = ls.Reader
    monkeypatch.setattr(ls, "Reader", lambda token_value=None: real_reader(token_value, url="https://x/api/v1", session=server))
    monkeypatch.setattr(ls, "reader", lambda **kw: ls.Reader(ls.token()[0]))
    asst = types.SimpleNamespace(db_path=tmp_path / "idx.sqlite")

    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "Bearer " + TOKEN)
    ei._scilog_magic(asst, "--scilog-token", "")
    out = capsys.readouterr().out
    assert "copy(localStorage.id_token)" in out and "Ann Example <ann@psi.ch>" in out and "3 logbooks readable" in out
    assert ls.token()[0] == TOKEN

    ei._scilog_magic(asst, "--scilog-logbooks", "--location bernina")
    out = capsys.readouterr().out
    assert "ARAMIS-BERNINA   p23618  26j_kern_p23618" in out and "-- 2 logbooks" in out

    ei._scilog_magic(asst, "--scilog-index", "")
    assert "which logbooks?" in capsys.readouterr().out
    ei._scilog_magic(asst, "--scilog-index", "--location bernina")
    out = capsys.readouterr().out
    assert "reading SciLog as Ann Example <ann@psi.ch>" in out and "3 new SciLog posts from 2 logbooks" in out
    ei._scilog_magic(asst, "--scilog-index", "")
    out = capsys.readouterr().out
    assert "the last choice: {'location': 'bernina'}" in out and "0 new SciLog posts" in out
    ei._scilog_magic(asst, "--scilog-index", "--rebuild")
    out = capsys.readouterr().out
    assert "fetching all posts again" in out and "3 new SciLog posts from 2 logbooks" in out
    assert ls.load_selection(asst.db_path) == {"location": "bernina"}

    ei._scilog_magic(asst, "--scilog-search", "laser freespace")
    out = capsys.readouterr().out
    assert "26j_kern_p23618  ann@psi.ch\n    Did the timing of the freespace laser" in out and "-- 1 posts" in out

    server.token = "expired.to.ken"
    ei._scilog_magic(asst, "--scilog-search", "laser")
    assert "SciLog refused the token" in capsys.readouterr().out
    ei._scilog_magic(asst, "--scilog-logbooks", "--bogus 1")
    assert "unknown option --bogus" in capsys.readouterr().out
