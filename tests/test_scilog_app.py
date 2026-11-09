"""eco.scilog_app: posts cleaned and pointed at the local server, kept up to
date incrementally, served only with the token, plots in their own origin."""
import builtins
import http.cookiejar
import json
import urllib.error
import urllib.request

import pytest

from _light_eco import ensure_eco_importable

ensure_eco_importable()

pytest.importorskip("tornado")

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

    def posts(self, logbook, updated_after=None):
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
