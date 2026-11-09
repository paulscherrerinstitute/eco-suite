"""eco.scilog_app: posts cleaned and pointed at the local server, kept up to
date incrementally, served only with the token, plots in their own origin."""
import builtins
import datetime
import http.cookiejar
import json
import urllib.error
import urllib.request

import pytest

from _light_eco import ensure_eco_importable

ensure_eco_importable()

pytest.importorskip("tornado")

from eco import logs_scilog
from eco.scilog_app import content
from eco.scilog_app.server import ScilogApp, Store

LB = "64ee4bd69e8ab0fb8ecdbb6d"
HOSTILE = (
    '<h2 onclick="x()">Setup</h2><script>alert(1)</script>'
    '<p style="color:red;position:fixed;background:url(x)">a</p><a href="javascript:x()">j</a>'
    '<figure class="image image_resized" style="width:50%"><img src="" title="H1" width="600" height="300"'
    ' style="aspect-ratio:600/300;"></figure>'
    '<p><a class="fileLink" target="_blank" href="file:H2">plot.html</a> <a class="fileLink" href="file:H3">d.json</a></p>'
)
FILES = [
    {"fileHash": "H1", "fileId": "a" * 24, "fileExtension": "image/png"},
    {"fileHash": "H2", "fileId": "b" * 24, "fileExtension": "file/html"},
    {"fileHash": "H3", "fileId": "c" * 24, "fileExtension": "file/json"},
]


def _check(html):
    assert "<script" not in html and "alert" not in html and "onclick" not in html and "javascript:" not in html
    assert '<p style="color:red">a</p>' in html  # colour kept, positioning and url() dropped
    assert '<figure class="image image_resized" style="width:50%">' in html
    assert f'<img src="img/{"a" * 24}" width="600" height="300" style="aspect-ratio:600/300" loading="lazy"' in html
    assert f'<span class="plot" data-file="{"b" * 24}" data-name="plot.html">' in html
    assert f'href="file/{"c" * 24}?download=1"' in html


def test_a_post_is_cleaned_and_points_at_the_server():
    pytest.importorskip("nh3")
    _check(content.clean_post(HOSTILE, FILES))


def test_without_nh3_bleach_cleans_the_same(monkeypatch):
    pytest.importorskip("bleach")
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n)) if n == "nh3" else real(n, *a, **k))
    _check(content.clean_post(HOSTILE, FILES))


class _FakeReader:
    identity = "Ann Example <ann@psi.ch> (token from test)"

    def __init__(self):
        self.rows = [self.row("p1", "2026-11-01T10:00:00Z", "<h2>First</h2><p>alpha</p>"),
                     self.row("p2", "2026-11-02T10:00:00Z", "<p>beta</p>")]
        self.requests = []

    @staticmethod
    def row(i, t, html, updated=None, deleted=False):
        return {"id": i, "createdAt": t, "updatedAt": updated or t, "createdBy": "ann@psi.ch", "updatedBy": "ann@psi.ch",
                "tags": ["ARAMIS"], "textcontent": html, "files": [], "deleted": deleted, "parentId": LB}

    def posts(self, logbook, updated_after=None, children=False):
        self.requests.append(updated_after)
        return [r for r in self.rows if not updated_after or r["updatedAt"] > updated_after]

    def logbooks(self):
        return [{"id": LB, "name": "Test log", "pgroup": "p1", "location": "x", "private": False, "touched": ""}]

    def image(self, file_id):
        return b"\x89PNG", "image/png"

    def file(self, file_id):
        return b"<html><script>1</script></html>", "text/html", "plot.html"


def test_the_store_reads_a_logbook_once_then_only_what_changed(tmp_path):
    reader = _FakeReader()
    store = Store(reader, cache_dir=tmp_path / "files")
    first = store.posts(LB)
    assert [p["id"] for p in first["posts"]] == ["p1", "p2"] and first["posts"][0]["title"] == "First"
    assert first["since"] == "2026-11-02T10:00:00Z"
    assert store.posts(LB, first["since"])["posts"] == []
    reader.rows[1] = reader.row("p2", "2026-11-02T10:00:00Z", "<p>beta, edited</p>", updated="2026-11-03T09:00:00Z")
    reader.rows.append(reader.row("p3", "2026-11-03T08:00:00Z", "<p>gamma</p>"))
    reader.rows[0] = reader.row("p1", "2026-11-01T10:00:00Z", "", updated="2026-11-03T09:30:00Z", deleted=True)
    later = store.posts(LB, first["since"])
    assert [p["id"] for p in later["posts"]] == ["p2", "p3"] and later["removed"] == ["p1"]
    assert later["posts"][0]["edited"] and "edited" in later["posts"][0]["text"]
    assert reader.requests[-1] == "2026-11-02T10:00:00Z"
    data, ctype, _ = store.image("a" * 24)
    assert data == b"\x89PNG" and oct((tmp_path / "files").stat().st_mode & 0o777) == "0o700"


@pytest.fixture
def server(tmp_path):
    app = ScilogApp(port=0, store=Store(_FakeReader(), cache_dir=tmp_path / "files"), token="sesame").start()
    yield app
    app.stop()


def test_the_server_needs_the_token_and_sandboxes_plots(server):
    base = f"http://localhost:{server.port}"
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base}/api/logbooks")
    assert err.value.code == 403
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    assert opener.open(f"{base}/?token=sesame").status == 200  # token -> cookie
    assert json.load(opener.open(f"{base}/api/logbooks"))["logbooks"][0]["name"] == "Test log"
    posts = json.load(opener.open(f"{base}/api/posts?logbook={LB}"))
    assert len(posts["posts"]) == 2
    plot = opener.open(f"{base}/file/{'b' * 24}")
    assert plot.headers["Content-Security-Policy"] == "sandbox allow-scripts"
    download = opener.open(f"{base}/file/{'b' * 24}?download=1")
    assert download.headers["Content-Type"] == "application/octet-stream" and "attachment" in download.headers["Content-Disposition"]
    with pytest.raises(urllib.error.HTTPError):
        opener.open(f"{base}/api/posts?logbook=../../etc")


# -- writing -----------------------------------------------------------------------------------

import base64  # noqa: E402

from eco.scilog_app.server import load_config  # noqa: E402

WIDE = ('<figure class="image image_resized"><img src="" title="H1" width="1483" height="556" '
        'style="aspect-ratio:1483/556;"></figure>')


def test_the_width_scilog_keeps_in_files_reaches_the_figure():
    files = [dict(FILES[0], style={"width": "51.2%", "height": ""})]
    html = content.clean_post(WIDE, files)
    assert '<figure class="image image_resized" style="width:51.2%">' in html


def test_editor_html_goes_back_to_what_scilog_stores():
    files = [dict(FILES[0], style={"width": "300px", "height": ""}), FILES[2]]
    edited = content.editor_html(WIDE + '<p><a class="fileLink" href="file:H3">d.json</a></p>', files)
    assert f'src="img/{"a" * 24}"' in edited and f'href="file/{"c" * 24}"' in edited
    edited = edited.replace("width:300px", "width:420px")  # resized with the mouse
    text, out = content.to_scilog(edited, {f["fileId"]: f for f in files})
    assert 'style="width:420px"' in text and 'title="H1"' in text and 'src=""' in text
    assert 'href="file:H3"' in text and 'class="fileLink"' in text
    assert out[0]["style"] == {"width": "420px", "height": ""} and out[1]["fileHash"] == "H3"
    with pytest.raises(content.Unresolved, match="still being uploaded"):
        content.to_scilog('<p><img src="data:image/png;base64,AAAA"></p>', {})
    with pytest.raises(content.Unresolved, match="not a file of this post"):
        content.to_scilog(f'<figure class="image"><img src="img/{"d" * 24}"></figure>', {})


def test_a_heading_level_changes_in_place():
    text = "<h2>A</h2><p>x</p><h3 class='c'>B</h3><h2>C</h2>"
    assert content.set_heading(text, 1, "h4") == "<h2>A</h2><p>x</p><h4 class='c'>B</h4><h2>C</h2>"
    assert content.set_heading(text, 2, "p") == "<h2>A</h2><p>x</p><h3 class='c'>B</h3><p>C</p>"
    with pytest.raises(ValueError):
        content.set_heading(text, 3, "h2")


def _token(email="ann@psi.ch", roles=("p1",)):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email, "roles": list(roles)}).encode()).decode().rstrip("=")
    return f"x.{payload}.y"


class _Writer(_FakeReader):
    def __init__(self, identity="Ann <ann@psi.ch> (token from test)", roles=("p1",)):
        super().__init__()
        self.identity, self.token = identity, _token(roles=roles)
        self.written = []
        self.rows[0]["updateACL"] = ["p1"]
        self.rows[1]["createdBy"], self.rows[1]["updateACL"] = "bob@psi.ch", ["p2"]

    def logbook(self, logbook_id):
        return {"ownerGroup": "p1", "accessGroups": ["p1", "unx-x"]}

    def upload_file(self, data, filename, content_type, fields):
        self.written.append(("upload", filename, fields))
        return {"id": "e" * 24, "accessHash": "hash"}

    def create_post(self, payload):
        self.written.append(("create", payload))
        row = self.row("p9", "2026-11-04T10:00:00Z", payload["textcontent"])
        row.update(files=payload["files"], tags=payload["tags"], updateACL=["p1"])
        self.rows.append(row)
        return row

    def update_post(self, post_id, payload):
        self.written.append(("update", post_id, payload))

    def post(self, post_id):
        return next(r for r in self.rows if r["id"] == post_id)


def test_writing_goes_only_where_it_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    reader = _Writer()
    store = Store(reader, cache_dir=tmp_path / "f", config={"write_logbooks": [LB]})
    posts = {p["id"]: p for p in store.posts(LB)["posts"]}
    assert posts["p1"]["can_edit"] and not posts["p2"]["can_edit"]  # p2: bob's, another group

    up = store.upload(LB, None, "shot.png", b"\x89PNG", "image/png")
    assert up == {"fileId": "e" * 24, "url": f"img/{'e' * 24}", "name": "shot.png", "image": True}
    assert reader.written[-1][2] == {"ownerGroup": "p1", "accessGroups": ["p1", "unx-x"], "fileExtension": "png"}
    new = store.save(LB, None, f'<h2>New</h2><figure class="image image_resized" style="width:300px"><img src="{up["url"]}"></figure>',
                     ["eco-test", " "])
    kind, payload = reader.written[-1]
    assert kind == "create" and payload["parentId"] == LB and payload["tags"] == ["eco-test"]
    assert payload["files"][0]["fileId"] == "e" * 24 and payload["files"][0]["style"]["width"] == "300px"
    assert new["id"] == "p9" and new["title"] == "New"

    with pytest.raises(PermissionError, match="only your own"):
        store.save(LB, "p2", "<p>not mine</p>", [])
    store.save(LB, "p1", "<p>mine, edited</p>", ["t"], expected_updated="2026-11-01T10:00:00Z")
    assert reader.written[-1][:2] == ("update", "p1")
    with pytest.raises(RuntimeError, match="changed meanwhile"):
        store.save(LB, "p1", "<p>again</p>", [], expected_updated="2020-01-01T00:00:00Z")

    other = Store(_Writer(), cache_dir=tmp_path / "g", config={"write_logbooks": ["f" * 24]})
    with pytest.raises(PermissionError, match="not switched on"):
        other.save(LB, None, "<p>x</p>", [])
    beamline = Store(_Writer(identity="the beamline account swissfelaramis-bernina@psi.ch"), cache_dir=tmp_path / "h",
                     config={"write_logbooks": "all"})
    with pytest.raises(PermissionError, match="personal token"):
        beamline.save(LB, None, "<p>x</p>", [])
    assert load_config(tmp_path / "missing.json")["write_logbooks"] == ["68afe9fe6f90f96e7370f9b3"]


def test_a_write_needs_the_page_s_own_header(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    app = ScilogApp(port=0, store=Store(_Writer(), cache_dir=tmp_path / "f", config={"write_logbooks": "all"}), token="sesame").start()
    try:
        base = f"http://localhost:{app.port}"
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        opener.open(f"{base}/?token=sesame")
        body = json.dumps({"logbook": LB, "html": "<p>hello</p>", "tags": []}).encode()
        with pytest.raises(urllib.error.HTTPError) as err:
            opener.open(urllib.request.Request(f"{base}/api/save", data=body, method="POST"))
        assert err.value.code == 403
        req = urllib.request.Request(f"{base}/api/save", data=body, method="POST",
                                     headers={"X-Eco-Scilog": "1", "Content-Type": "application/json"})
        assert json.load(opener.open(req))["id"] == "p9"
        evil = urllib.request.Request(f"{base}/api/save", data=body, method="POST",
                                      headers={"X-Eco-Scilog": "1", "Origin": "http://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as err:
            opener.open(evil)
        assert err.value.code == 403
    finally:
        app.stop()


def test_writing_is_switched_on_per_logbook_and_remembered(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    cfg_path = tmp_path / "scilog_app.json"
    cfg_path.write_text(json.dumps({"other": 1}))
    store = Store(_Writer(), cache_dir=tmp_path / "f", config=load_config(cfg_path))
    store.config_path = cfg_path
    lb = "a" * 24
    assert not store.may_write_to(lb)[0]
    me = store.set_write_logbook(lb)
    assert lb in me["write_logbooks"] and "roles" not in me and store.may_write_to(lb)[0]
    saved = json.loads(cfg_path.read_text())
    assert saved["other"] == 1 and lb in saved["write_logbooks"]  # other settings kept
    assert (cfg_path.stat().st_mode & 0o777) == 0o600
    store.set_write_logbook(lb, on=False)
    assert not store.may_write_to(lb)[0]
    with pytest.raises(ValueError):
        store.set_write_logbook("../etc")
    beamline = Store(_Writer(identity="the beamline account x@psi.ch"), cache_dir=tmp_path / "h", config={"write_logbooks": []})
    with pytest.raises(PermissionError, match="personal token"):
        beamline.set_write_logbook(lb)


def _view(i, name, others, parent=LB, title="Logbook view"):
    return {"id": i, "snippetType": "view", "name": name, "parentId": parent, "ownerGroup": "p19734", "deleted": False,
            "configuration": {"isTemplate": False, "widgetConfig": [
                {"cols": 4, "rows": 4, "x": 1, "y": 0, "config": {"general": {"type": "logbook", "title": title},
                 "filter": {"targetId": parent, "additionalLogbooks": others, "tags": [], "excludeTags": []},
                 "view": {"order": ["defaultOrder ASC"]}}},
                {"cols": 1, "rows": 4, "x": 0, "y": 0, "config": {"general": {"type": "tasks", "title": "Beamline tasks"},
                 "filter": {"snippetType": ["task"]}, "view": {}}}]}}


class _ViewReader(_Writer):
    def __init__(self, views):
        super().__init__()
        self.stored = {v["id"]: v for v in views}

    def logbook(self, logbook_id):
        return {"ownerGroup": "p1", "accessGroups": ["p1"], "location": "c" * 24}

    def logbooks(self):
        return [{"id": LB, "name": "Test log"}, {"id": "a" * 24, "name": "Run A"}, {"id": "b" * 24, "name": "Run B"}]

    def views(self, parents):
        return [v for v in self.stored.values() if v["parentId"] in parents]

    def view(self, view_id):
        return json.loads(json.dumps(self.stored[view_id]))

    def update_view(self, view_id, payload):
        self.written.append(("update_view", view_id, payload))
        self.stored[view_id].update(json.loads(json.dumps(payload)))

    def delete_view(self, view_id):
        self.written.append(("delete_view", view_id))
        del self.stored[view_id]


def test_scilog_views_are_listed_told_apart_changed_and_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    old = _view("68baa3c86f90f983d570fe6f", "general", ["a" * 24])
    new = _view("68baa3ee6f90f9107e70fe70", "general", ["a" * 24, "b" * 24, "b" * 24, "d" * 24])
    elsewhere = _view("68baa3ee6f90f9107e70fe71", "other", [], parent="e" * 24)
    reader = _ViewReader([old, new, elsewhere])
    store = Store(reader, cache_dir=tmp_path / "cache" / "files", config={"write_logbooks": [LB]})

    listed = store.views(LB)
    assert [v["id"] for v in listed["views"]] == [old["id"], new["id"]] and listed["can_edit"]
    panel = listed["views"][1]["panels"][0]
    assert panel["type"] == "logbook" and panel["duplicates"] == 1  # "b" listed twice
    assert [(l["name"], l["readable"]) for l in panel["logbooks"]] == [("Run A", True), ("Run B", True), ("", False)]
    assert listed["views"][0]["created"] == 0x68baa3c8

    version = listed["views"][1]["version"]
    out = store.change_view(LB, new["id"], version, {"name": "general (2026)", "panels": [{"index": 0, "logbooks": ["a" * 24, "b" * 24], "tags": [" x ", ""]}]})
    kind, vid, payload = reader.written[-1]
    assert kind == "update_view" and payload["name"] == "general (2026)"
    w = payload["configuration"]["widgetConfig"]
    assert w[0]["config"]["filter"]["additionalLogbooks"] == ["a" * 24, "b" * 24] and w[0]["config"]["filter"]["tags"] == ["x"]
    assert w[0]["cols"] == 4 and w[1] == new["configuration"]["widgetConfig"][1]  # layout and the other panel kept
    assert out["name"] == "general (2026)" and out["version"] != version
    backups = list((tmp_path / "cache" / "scilog_view_backups").glob(f"{new['id']}-*.json"))
    assert backups and json.loads(backups[0].read_text())["name"] == "general"
    assert (backups[0].stat().st_mode & 0o777) == 0o600

    with pytest.raises(RuntimeError, match="changed meanwhile"):
        store.change_view(LB, new["id"], version, {"name": "again"})
    with pytest.raises(PermissionError, match="does not belong"):
        store.change_view(LB, elsewhere["id"], None, {"name": "x"})
    with pytest.raises(ValueError):
        store.change_view(LB, old["id"], None, {"panels": [{"index": 0, "logbooks": ["../x"]}]})
    with pytest.raises(ValueError, match="nothing to change"):
        store.change_view(LB, old["id"], None, {})

    assert store.remove_view(LB, old["id"], store.views(LB)["views"][0]["version"]) == {"deleted": old["id"], "name": "general"}
    assert old["id"] not in reader.stored

    readonly = Store(_ViewReader([_view("68baa3c86f90f983d570fe6f", "general", [])]), cache_dir=tmp_path / "g",
                     config={"write_logbooks": []})
    assert not readonly.views(LB)["can_edit"]
    with pytest.raises(PermissionError, match="not switched on"):
        readonly.change_view(LB, "68baa3c86f90f983d570fe6f", None, {"name": "x"})


class _Threads(_Writer):
    """Posts with SciLog's order, a comment, a reply quoting another post, and comments arriving later."""

    A, B, R = "a1" * 12, "b2" * 12, "c3" * 12

    def __init__(self):
        super().__init__()
        a = self.row(self.A, "2026-11-01T10:00:00Z", "<h1>Run</h1><p>offset fixed?</p>")
        b = self.row(self.B, "2026-11-01T12:00:00Z", "<p>later</p>")
        r = self.row(self.R, "2026-11-01T13:00:00Z", "<p>yes, fixed</p>")
        for row in (a, b, r):
            order = int(datetime.datetime.fromisoformat(row["createdAt"].replace("Z", "+00:00")).timestamp() * 1e6)
            row.update(defaultOrder=order, expiresAt="2026-11-04T10:00:00Z", updateACL=["p1"])
        a["subsnippets"] = [{"id": "d4" * 12, "parentId": self.A, "linkType": "comment", "createdAt": "2026-11-01T11:00:00Z",
                             "createdBy": "bob@psi.ch", "textcontent": "<p>looks good</p>", "files": [], "deleted": False}]
        # a reply: the copy of what it answers carries no link, only the same text (and the original's expiry)
        r["subsnippets"] = [{"id": "e5" * 12, "parentId": self.R, "linkType": "quote", "createdAt": "2026-11-01T13:00:00Z",
                             "createdBy": "ann@psi.ch", "textcontent": "<h1>Run</h1><p>offset fixed?</p>", "files": [], "deleted": False,
                             "expiresAt": "2026-11-04T10:00:00Z"}]
        self.rows = [a, b, r]
        self.kids = []

    def children_since(self, updated_after):
        return [k for k in self.kids if k["updatedAt"] > updated_after]

    def create_post(self, payload):
        row = super().create_post(payload)
        row["id"] = "f6" * 12
        row.update(createdAt="2026-11-06T08:00:00Z", updatedAt="2026-11-06T08:00:00Z", defaultOrder=payload.get("defaultOrder", 1800000000000000), expiresAt=payload.get("expiresAt"))
        return row


def test_comments_replies_and_scilogs_order_reach_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    reader = _Threads()
    store = Store(reader, cache_dir=tmp_path / "files", config={"write_logbooks": [LB]})
    first = store.posts(LB)
    a, b, r = first["posts"]
    assert [c["text"].strip() for c in a["comments"]] == ["looks good"] and a["comments"][0]["author"] == "bob@psi.ch"
    assert r["quote"]["of"] == reader.A and "offset fixed?" in r["quote"]["text"] and not b["quote"]
    assert a["t"] == a["written"] and not a["inserted"] and a["expires"] == datetime.datetime(2026, 11, 4, 10, tzinfo=datetime.timezone.utc).timestamp()

    # a comment written later does not touch its post's updatedAt; it is found all the same
    reader.kids = [{"id": "a7" * 12, "parentId": reader.B, "linkType": "comment", "createdAt": "2026-11-05T09:00:00Z",
                    "updatedAt": "2026-11-05T09:00:00Z", "createdBy": "ann@psi.ch", "textcontent": "<p>and now?</p>", "files": [], "deleted": False}]
    later = store.posts(LB, first["since"])
    assert [p["id"] for p in later["posts"]] == [reader.B] and later["posts"][0]["comments"][0]["text"].strip() == "and now?"
    assert store.posts(LB, later["since"])["posts"] == []  # seen once

    # a post inserted after A lands between A and B in SciLog's order, with the edit period asked for
    saved = store.save(LB, None, "<h2>Alignment</h2>", [], edit_days=30, after=reader.A)
    payload = reader.written[-1][1]
    assert a["order"] < payload["defaultOrder"] < b["order"]
    until = datetime.datetime.fromisoformat(payload["expiresAt"].replace("Z", "+00:00"))
    assert 29.9 < (until - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 86400 <= 30
    assert saved["inserted"] and a["t"] < saved["t"] < b["t"]
    with pytest.raises(ValueError):
        store.save(LB, None, "<p>x</p>", [], edit_days=0)
    with pytest.raises(ValueError):
        store.save(LB, None, "<p>x</p>", [], after="../x")


def test_sharing_with_everyone_logged_in_covers_files_and_comments_first(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    reader = _Threads()
    a = reader.rows[0]
    a.update(readACL=["p1"], files=[{"fileId": "ab" * 12}])
    a["subsnippets"][0]["readACL"] = ["p1"]
    reader.rows[1].update(createdBy="bob@psi.ch", updateACL=["p2"])
    reader.files = {"ab" * 12: {"readACL": ["p1"]}}
    reader.file_snippet = lambda f: reader.files[f]
    reader.set_read_acl = lambda kind, i, acl: reader.written.append(("acl", kind, i, acl))
    reader.post = lambda i: json.loads(json.dumps(next(r for r in reader.rows if r["id"] == i)))
    store = Store(reader, cache_dir=tmp_path / "files", config={"write_logbooks": [LB]})
    assert not store.posts(LB)["posts"][0]["shared"]

    out = store.share(LB, reader.A, True)
    acl = [w for w in reader.written if w[0] == "acl"]
    assert [(k, i) for _, k, i, _ in acl] == [("filesnippet", "ab" * 12), ("basesnippets", "d4" * 12), ("basesnippets", reader.A)]
    assert all("any-authenticated-user" in w[3] and "p1" in w[3] for w in acl) and out["shared"]

    # once shared, sharing again changes nothing; unsharing removes only that role
    a["readACL"] = acl[-1][3]
    reader.files["ab" * 12]["readACL"] = acl[0][3]
    n = len(reader.written)
    store.share(LB, reader.A, True)
    assert len(reader.written) == n
    out = store.share(LB, reader.A, False)
    assert [w[3] for w in reader.written[n:]] == [["p1"], ["p1"], ["p1"]] and not out["shared"]

    # someone else's post: SciLog would refuse, so does the viewer
    with pytest.raises(PermissionError):
        store.share(LB, reader.B, True)


def test_deleting_needs_a_delete_group_and_takes_the_post_off_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    reader = _Threads()
    reader.rows[0]["deleteACL"] = ["admin", "p1"]
    reader.rows[1]["deleteACL"] = ["admin"]
    reader.delete_post = lambda i: reader.written.append(("delete", i))
    store = Store(reader, cache_dir=tmp_path / "files", config={"write_logbooks": [LB]})
    a, b, _ = store.posts(LB)["posts"]
    assert a["can_delete"] and not b["can_delete"]
    assert store.delete(LB, reader.A) == {"removed": reader.A} and reader.written[-1] == ("delete", reader.A)
    assert reader.A not in [p["id"] for p in store.posts(LB)["posts"]]
    with pytest.raises(PermissionError):
        store.delete(LB, reader.B)
    store2 = Store(reader, cache_dir=tmp_path / "files2", config={"write_logbooks": []})
    with pytest.raises(PermissionError):
        store2.delete(LB, reader.B)


def test_the_token_is_checked_with_scilog_before_it_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    reader = _Writer()
    store = Store(reader, cache_dir=tmp_path / "files", config={"write_logbooks": [LB]})
    store.logbooks["x"] = {}
    status = store.token_status()
    assert status["ok"] and status["personal"] and status["can_set"]

    def claim(**c):
        body = base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip("=")
        return f"x.{body}.y"

    with pytest.raises(ValueError, match="not a SciLog token"):
        store.set_token("nonsense")
    with pytest.raises(ValueError, match="expired"):
        store.set_token(claim(exp=1))
    kept, probed = [], []

    class Probe:
        def __init__(self, value, timeout=30):
            self.value = value

        def get(self, path, filt=None):
            probed.append(self.value)
            if self.value.endswith("refused"):
                raise logs_scilog.TokenError("SciLog refused the token")

    monkeypatch.setattr("eco.logs_scilog.Reader", Probe)
    monkeypatch.setattr("eco.logs_scilog.save_token", lambda v, path=None: kept.append(v))
    with pytest.raises(logs_scilog.TokenError):
        store.set_token(claim(exp=4102444800)[:-1] + "refused")
    assert kept == []  # refused by SciLog: not kept
    good = claim(exp=4102444800, email="ann@psi.ch")
    monkeypatch.setattr(Store, "token_status", lambda self: {"ok": True})
    store.set_token(" Bearer " + good + "\n")
    assert kept == [good] and probed[-1] == good and store.logbooks == {} and store._reader is None


def test_pages_asked_for_under_another_name_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("eco.logs_scilog._shared_account", lambda: None)
    app = ScilogApp(port=0, store=Store(_Writer(), cache_dir=tmp_path / "files", config={}), token="sesame").start()
    try:
        for host, code in (("localhost", 200), ("127.0.0.1", 200), ("evil.example", 403)):
            req = urllib.request.Request(f"http://127.0.0.1:{app.port}/api/token?token=sesame", headers={"Host": f"{host}:{app.port}"})
            try:
                got = urllib.request.urlopen(req, timeout=10).status
            except urllib.error.HTTPError as e:
                got = e.code
            assert got == code, host
    finally:
        app.stop()
