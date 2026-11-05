"""eco.ei_page: a %ei answer as a page, SciLog posts read again with their
neighbours and images, untrusted HTML cleaned, citations linked."""
import re
import time

import pytest

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.ei as ei
import eco.ei_page as ep
import eco.logs_ask as la

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


class _FakeScilog:
    """Five posts in one logbook; p3 has an image and some hostile HTML."""

    def __init__(self):
        self.posts = [
            {"id": f"p{i}", "parentId": "lb1", "createdAt": f"2026-05-08T1{i}:00:00Z", "createdBy": "ann",
             "tags": ["ARAMIS"] if i == 3 else [], "textcontent": f"<p>post {i}</p>", "files": []}
            for i in range(1, 6)
        ]
        self.posts[2]["textcontent"] = (
            '<h2 onclick="steal()">Setup</h2><script>alert(1)</script>'
            '<p>detector image:</p><img style="x" src="" width="60" height="40" title="hash1">'
            '<table><tr><td>run</td><td>837</td></tr></table><a href="javascript:evil()">x</a>'
        )
        self.posts[2]["files"] = [{"fileHash": "hash1", "fileId": "f1"}]
        self.images = []

    def post(self, post_id):
        return next(p for p in self.posts if p["id"] == post_id)

    def around(self, post, n=3):
        i = self.posts.index(post)
        return self.posts[max(0, i - n):i + n + 1]

    def image(self, file_id):
        self.images.append(file_id)
        return PNG, "image/png"


def _hit(post="p3"):
    t = time.mktime((2026, 5, 8, 13, 0, 0, 0, 0, -1))
    return la.Hit("scilog", "Setup detector image", "ann [lb]", "ok", "", t, "scilog lb", 1.0, f"scilog:lb1:{post}")


def test_a_scilog_source_is_shown_as_in_scilog_between_its_neighbours(tmp_path):
    pytest.importorskip("nh3")
    hit = _hit()
    ans = ei.Answer("which run?", f"Run 837, see {hit.cite()}.\n\n```python\nx = [1]\n```", [hit], "m", 1.0)
    scilog = _FakeScilog()
    path = ep.write(ans, directory=tmp_path, reader=scilog, around=1)
    page = path.read_text()

    assert page.count('<article class="post') == 3 and page.count('class="post cited"') == 1
    assert "post 2" in page and "post 4" in page and "post 1" not in page
    assert "<script" not in page and "onclick" not in page and "javascript:" not in page
    assert "<table>" in page and '<span class="tag">ARAMIS</span>' in page
    image = path.with_name(path.stem + "_files") / "f1.png"
    assert image.read_bytes() == PNG and f'src="{image.parent.name}/f1.png"' in page
    assert scilog.images == ["f1"]
    assert re.search(r'<a class="cite" href="#src-1">\[scilog 2026-05-08', page)
    assert 'id="src-1"' in page and "<pre><code" in page  # the answer's code stays code
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_without_scilog_the_indexed_text_is_shown(tmp_path):
    class Down:
        def post(self, post_id):
            raise ConnectionError("no route to scilog.psi.ch")

    hit = _hit()
    ans = ei.Answer("which run?", "listing", [hit], "", 0.1)  # a search-only answer
    page = ep.render(ans, db_path=tmp_path / "none.sqlite", reader=Down())
    assert "SciLog could not be read (ConnectionError: no route" in page
    assert "Setup detector image" in page and "search only" in page
    assert ep.render(ans, db_path=tmp_path / "none.sqlite", reader=False).count("could not be read") == 0


def test_short_and_nested_citations_are_linked():
    hits = [_hit("p3"), la.Hit("doc", "text", "scanning.md > Mesh", "ok", "", 0, "doc", 1.0, "/x/scanning.md")]
    rx, numbers = ep._citation_rx(hits, ei._citation_forms)
    text = "as in [scilog 2026-05-08 13:00 ann [lb]] and [scilog 2026-05-08] and [doc scanning.md &gt; Mesh] or [doc scanning.md]"
    found = [numbers[[g is not None for g in m.groups()].index(True)] for m in rx.finditer(text)]
    assert found == [1, 1, 2, 2]


def test_old_pages_are_removed_with_their_images(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "KEEP", 2)
    for i in range(4):
        (tmp_path / f"2026010{i}_000000_q.html").write_text("x")
        (tmp_path / f"2026010{i}_000000_q_files").mkdir()
    ep.write(ei.Answer("q", "nothing", [], "m", 0.0), directory=tmp_path, reader=False)
    assert len(list(tmp_path.glob("*.html"))) == 2 and len(list(tmp_path.glob("*_files"))) == 1


def test_the_magic_writes_and_opens_the_last_answer(tmp_path, monkeypatch, capsys):
    pytest.importorskip("IPython")
    import IPython

    monkeypatch.setattr(IPython, "get_ipython", lambda: None)
    a = ei.Assistant(None, config=ei.load_config(tmp_path / "ei.json"), db_path=tmp_path / "i.sqlite")
    monkeypatch.setitem(ei._assistants, id(None), a)
    ei.ei_magic("-b")
    assert "nothing to show yet" in capsys.readouterr().out
    a.shown = ei.Answer("q", "an answer", [], "m", 0.0)
    monkeypatch.setattr(ep, "PAGES_DIR", tmp_path / "pages")
    opened = []
    monkeypatch.setattr(ep, "open_page", lambda path: opened.append(path) or True)
    ei.ei_magic("--browser")
    assert opened and opened[0].parent == tmp_path / "pages" and "eco.ei: opened" in capsys.readouterr().out
