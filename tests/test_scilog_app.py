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
