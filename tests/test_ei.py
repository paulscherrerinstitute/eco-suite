"""eco.ei (%ei): API key lookup, namespace context without initialising
anything, retrieval and prompt, follow-ups, the streamed answer from an
OpenAI-style server (a local fake), and the magic's options."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.ei as ei
import eco.logs_ask as la


# -- fixtures ---------------------------------------------------------------------


def _index(tmp_path):
    # the package directory's name is the module prefix: "eco.attenuator"
    logs, docs, src = tmp_path / "logs", tmp_path / "docs", tmp_path / "eco"
    for d in (logs, docs, src):
        d.mkdir()
    records = [
        {"t": 1.0, "event": "session_start", "kind": "console", "label": "bernina"},
        {"t": 2.0, "event": "input", "code": "att.set_transmission(0.1)"},
        {"t": 2.1, "event": "result", "text": "done"},
        {"t": 3.0, "event": "input", "code": "%ei how do I set the transmission?"},
        {"t": 3.1, "event": "stream", "text": "use att.set_transmission -- an answer, not history"},
    ]
    (logs / "20261020_090000_console_aaaa.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    (docs / "scanning.md").write_text("# Scanning\n\n## Mesh\n\nscans.scan((mot1, 0, 1, 10), (mot2, 0, 1, 5)) is a mesh scan.\n")
    (src / "attenuator.py").write_text(
        '"""module"""\n'
        "class AttenuatorAramis(Assembly):\n"
        '    """Solid attenuator of Aramis."""\n'
        "    def __init__(self, Id, name=None):\n        pass\n"
        "    def set_transmission(self, value, wait=True):\n"
        '        """Set the transmission (0..1)."""\n'
        "    def _private(self):\n        pass\n"
        "    def undocumented(self):\n        pass\n"
        "def _hidden():\n    pass\n"
        r'X = "\d"  # an invalid escape: no SyntaxWarning may leak' "\n"
    )
    # an analysis package with its docs and an example notebook
    esc, esc_docs, esc_examples = tmp_path / "escape", tmp_path / "escape-docs", tmp_path / "escape-examples"
    for d in (esc, esc_docs, esc_examples):
        d.mkdir()
    (esc / "array.py").write_text(
        "class Array:\n"
        '    """Event-indexed data."""\n'
        "    def digitize(self, bins):\n"
        '        """Categorize events into bins of this array."""\n'
    )
    (esc_docs / "array_operations.md").write_text("# Arrays\n\n## Binning\n\nUse `delay.digitize(bins).categorize(sig)` to bin events.\n")
    nb = {"cells": [
        {"cell_type": "markdown", "source": ["# Pump-probe\n", "\n", "## Step 1 - bin by delay\n"]},
        {"cell_type": "code", "source": ["binned = delay.digitize(bins)\n", "binned.categorize(sig)"]},
        {"cell_type": "code", "source": ["   "]},
    ]}
    (esc_examples / "pump_probe.ipynb").write_text(json.dumps(nb))
    (esc_examples / ".ipynb_checkpoints").mkdir()
    (esc_examples / ".ipynb_checkpoints" / "pump_probe-checkpoint.ipynb").write_text(json.dumps(nb))
    db = tmp_path / "idx.sqlite"
    la.build_index(
        db_path=db, log_dir=logs,
        doc_dirs=(docs, (esc_docs, "escape: ")),
        src_dirs=(src, esc),
        example_dirs=[(esc_examples, "escape: ")],
    )
    return db


def test_escape_sources_are_indexed_with_their_labels(tmp_path):
    db = _index(tmp_path)
    examples = la.search("digitize bins", k=10, db_path=db, kinds=("example",))
    assert [(h.context, h.text) for h in examples] == [
        ("escape: pump_probe.ipynb > Step 1 - bin by delay", "binned = delay.digitize(bins)\nbinned.categorize(sig)")
    ]  # the checkpoint copy and the blank cell are not indexed
    assert examples[0].cite() == "[example escape: pump_probe.ipynb > Step 1 - bin by delay]"
    api = la.search("digitize", k=10, db_path=db, kinds=("api",), context_prefix="escape.")
    assert {h.context for h in api} == {"escape.array.Array", "escape.array.Array.digitize"}
    assert not la.search("digitize", k=10, db_path=db, kinds=("api",), context_prefix="eco.")
    docs = la.search("binning", k=10, db_path=db, kinds=("doc",), exclude_prefix="escape: ")
    assert docs == []
    assert la.search("binning", k=10, db_path=db, kinds=("doc",))[0].context == "escape: array_operations.md > Binning"


def test_escape_questions_get_escape_context_and_the_primer(tmp_path, monkeypatch):
    backend = _FakeBackend()
    a = _assistant(tmp_path, backend)
    primer = tmp_path / "primer.md"
    primer.write_text("Arrays are indexed by pulse id.")
    a.config["topics"]["escape"]["primer"] = str(primer)
    # by a word of the topic, and by the matches (no topic word here)
    assert a.topic("escape: bin my data") == "escape"
    assert a.topic("how do I digitize by delay bins?") == "escape"
    assert a.topic("how do I set the attenuator transmission?") is None

    a.ask("how do I digitize by delay bins?", report=None)
    system, user = backend.calls[0][0]["content"], backend.calls[0][-1]["content"]
    assert "escape primer" in system and "indexed by pulse id" in system
    assert "[example escape: pump_probe.ipynb > Step 1 - bin by delay]\n```python\nbinned = delay.digitize(bins)" in user
    assert "[api escape.array.Array.digitize]" in user
    # a follow-up stays on the topic
    assert a.topic("and with 50 bins?") == "escape"

    a.ask("how do I set the attenuator transmission?", new=True, report=None)
    system, user = backend.calls[1][0]["content"], backend.calls[1][-1]["content"]
    assert "escape primer" not in system and "[api escape." not in user and "[api eco.attenuator" in user


def test_index_sources_find_packages_without_importing(tmp_path, monkeypatch):
    import importlib.util
    import sys
    import types

    pkg = tmp_path / "escape"
    repo = tmp_path / "escape-fel"
    for d in (pkg, repo / "docs", repo / "examples"):
        d.mkdir(parents=True)
    spec = types.SimpleNamespace(submodule_search_locations=[str(pkg)])
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: spec if name == "escape" else None)
    cfg = _config()
    cfg["sources"]["repos"]["escape: "] = [str(tmp_path / "missing"), str(repo)]
    cfg["sources"]["example_dirs"] = {"mine: ": [str(tmp_path / "nbs")]}
    src = ei.Assistant(None, config=cfg, db_path=tmp_path / "x.sqlite").index_sources()
    assert src["src_dirs"] == [la.SRC_DIR, pkg]
    assert src["doc_dirs"] == [la.DOCS_DIR, (repo / "docs", "escape: ")]
    assert src["example_dirs"] == [(pkg, "escape: "), (repo / "examples", "escape: "), (tmp_path / "nbs", "mine: ")]
    assert "escape" not in sys.modules or not isinstance(sys.modules["escape"], types.SimpleNamespace)


class _LazyProxy:
    """Stands in for eco.utilities.config.Proxy: any attribute access is
    'initialisation' and fails the test."""

    def __getattr__(self, item):
        raise AssertionError(f"lazy component touched: .{item}")


class _Leaf:
    def get_current_value(self):
        return 1

    def set_target_value(self, v):
        pass


class _Device:
    def _append(self):
        pass


class AttenuatorAramis:  # a factory, as Namespace._factory_info stores it
    pass


class _FakeNamespace:
    def __init__(self):
        mono = _Device()
        mono.alias = object()
        energy = _Leaf()
        energy.__dict__["alias"] = object()
        mono.energy = energy
        mono.not_a_component = 3
        self.initialized_items = {"mono": mono}
        self.lazy_items = {"att": _LazyProxy()}
        self.failed_items = {"broken": None}
        self._factory_info = {
            "att": {"obj_factory": AttenuatorAramis, "module_name": "eco.xoptics.attenuator_aramis"},
            "mono": {"obj_factory": "DoubleCrystalMono", "module_name": "eco.xoptics.dcm_new"},
            "broken": {"obj_factory": "Thing", "module_name": None},
        }

    @property
    def lazy_names(self):
        return set(self.lazy_items)

    @property
    def failed_names(self):
        return set(self.failed_items)

    @property
    def all_names(self):
        return set(self.lazy_items) | set(self.failed_items) | set(self.initialized_items)


class _FakeBackend:
    def __init__(self, reply="Use `att`:\n\n```python\natt.set_transmission(0.1)\n```\n"):
        self.reply, self.calls = reply, []

    def chat(self, messages, on_delta=None, stop=None):
        self.calls.append(messages)
        if on_delta:
            for piece in (self.reply[:5], self.reply[5:]):
                on_delta(piece)
        return self.reply


def _config():
    cfg = json.loads(json.dumps(ei.DEFAULT_CONFIG))
    cfg["reindex_interval"] = 1e9  # the test index is built by hand
    return cfg


def _assistant(tmp_path, backend=None, namespace=None):
    db = _index(tmp_path)
    a = ei.Assistant(namespace, config=_config(), backend=backend, db_path=db)
    a._indexed_at = 1e18
    return a


# -- index additions (logs_ask) ----------------------------------------------------


def test_api_chunks_and_skipped_ei_commands(tmp_path, recwarn):
    db = _index(tmp_path)
    assert not [w for w in recwarn if issubclass(w.category, SyntaxWarning)]
    api = {h.context: h.text for h in la.search("attenuator transmission", k=20, db_path=db, kinds=("api",))}
    cls = next(v for k, v in api.items() if k.endswith(".AttenuatorAramis"))
    assert "AttenuatorAramis(Id, name=None)" in cls and "Solid attenuator" in cls
    assert "set_transmission" in cls and "undocumented" in cls and "_private" not in cls
    assert any("set_transmission(value, wait=True): Set the transmission" in v for v in api.values())
    commands = [h.text for h in la.search("transmission", k=20, db_path=db, kinds=("command",))]
    assert commands == ["att.set_transmission(0.1)"]  # the %ei question is not history


def test_stopwords_are_not_searched():
    assert la._fts_query("How do I do a mesh scan?") == '"mesh"* OR "scan"*'
    assert la._fts_query("how do I") == ""


# -- configuration -------------------------------------------------------------------


def test_api_key_order(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "KEY_PATH", tmp_path / "ei_api_key")
    monkeypatch.setattr(ei, "_gopass_key", lambda: None)
    monkeypatch.setattr(ei.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("ECO_EI_API_KEY", raising=False)
    assert ei.api_key() == (None, None)

    (tmp_path / ".continue").mkdir()
    (tmp_path / ".continue" / "config.yaml").write_text(
        "models:\n"
        "  - {name: other, apiBase: 'https://elsewhere/api', apiKey: sk-wrong}\n"
        "  - {name: psi, apiBase: 'https://ai.psi.ch/api', apiKey: sk-continue}\n"
    )
    pytest.importorskip("yaml")
    assert ei.api_key() == ("sk-continue", "~/.continue/config.yaml")

    monkeypatch.setattr(ei, "_gopass_key", lambda: "sk-gopass")
    assert ei.api_key()[0] == "sk-gopass"

    (tmp_path / "ei_api_key").write_text("sk-file\n")
    (tmp_path / "ei_api_key").chmod(0o600)
    assert ei.api_key()[0] == "sk-file"

    monkeypatch.setenv("ECO_EI_API_KEY", "sk-env")
    assert ei.api_key() == ("sk-env", "$ECO_EI_API_KEY")


def test_config_file_is_created_and_merged(tmp_path):
    path = tmp_path / "ei.json"
    assert ei.load_config(path)["model"] == ei.DEFAULT_CONFIG["model"]
    assert json.loads(path.read_text()) == ei.DEFAULT_CONFIG
    path.write_text(json.dumps({"model": "other", "hits": {"api": 0}}))
    cfg = ei.load_config(path)
    assert cfg["model"] == "other" and cfg["hits"]["api"] == 0 and cfg["hits"]["doc"] == 6


# -- namespace context --------------------------------------------------------------


def test_namespace_info_never_touches_lazy_components():
    info = ei.NamespaceInfo(_FakeNamespace())
    assert info.entries() == [
        ("att", "AttenuatorAramis", "eco.xoptics.attenuator_aramis", "lazy"),
        ("broken", "Thing", "", "failed"),
        ("mono", "DoubleCrystalMono", "eco.xoptics.dcm_new", ""),
    ]
    assert info.expansions("set the attenuator transmission") == ["att"]
    assert info.expansions("change the monochromator energy") == ["mono"]
    text = info.text("what is in mono?")
    assert "att: AttenuatorAramis [lazy]" in text and "broken: Thing [failed]" in text
    assert "Components of mono:\nenergy: _Leaf (adjustable)" in text
    assert "not_a_component" not in text


# -- asking --------------------------------------------------------------------------


def test_ask_builds_context_and_keeps_follow_ups(tmp_path):
    backend = _FakeBackend()
    a = _assistant(tmp_path, backend, _FakeNamespace())
    pieces = []
    ans = a.ask("how do I set the attenuator transmission?", on_delta=pieces.append, report=None)
    assert "".join(pieces) == backend.reply == ans.text
    assert ans.code == "att.set_transmission(0.1)"
    system, user = backend.calls[0][0]["content"], backend.calls[0][-1]["content"]
    assert "Never invent device names" in system
    assert "att: AttenuatorAramis [lazy]" in user
    assert "(ok) >>> att.set_transmission(0.1)" in user
    assert "[api " in user and user.endswith("Question: how do I set the attenuator transmission?")

    a.ask("and for 20%?", report=None)
    msgs = backend.calls[1]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "how do I set the attenuator transmission?"
    # the follow-up alone has no searchable word; the previous question's do
    assert "att.set_transmission(0.1)" in msgs[-1]["content"]

    a.ask("something new", new=True, report=None)
    assert len(backend.calls[2]) == 2


def test_search_only_and_no_key(tmp_path, monkeypatch):
    backend = _FakeBackend()
    a = _assistant(tmp_path, backend)
    a.ask("attenuator transmission", report=None)
    ans = a.ask("mesh scan", search_only=True, report=None)
    assert len(backend.calls) == 1 and ans.model == ""
    assert "[doc scanning.md > Mesh]" in ans.text
    assert a.last.code == "att.set_transmission(0.1)"  # --paste still has the model's answer

    monkeypatch.setattr(ei, "api_key", lambda base_url=None: (None, None))
    (tmp_path / "b").mkdir()
    b = _assistant(tmp_path / "b", None)
    reports = []
    ans = b.ask("mesh scan", report=reports.append)
    assert "no API key" in reports[-1] and "scans.scan" in ans.text


def test_without_a_key_it_searches_and_says_so_once(tmp_path, monkeypatch):
    lookups = []
    monkeypatch.setattr(ei, "api_key", lambda base_url=None: lookups.append(1) or (None, None))
    a = _assistant(tmp_path, None)
    reports = []
    for _ in range(3):
        ans = a.ask("mesh scan", report=reports.append)
        assert ans.model == "" and "scans.scan" in ans.text
    assert "Get one at https://ai.psi.ch" in reports[0]
    assert reports[1:] == [ei.NO_KEY_SHORT, ei.NO_KEY_SHORT]
    assert len(lookups) == 1  # not looked up again for every question
    assert a.backend(recheck=True) is None and len(lookups) == 2


def test_eco_no_ei_disables_the_magic(monkeypatch):
    shell = _Shell()
    monkeypatch.setenv("ECO_NO_EI", "1")
    assert not ei.enabled()
    ei.register_magic(shell)
    assert shell.magics == {}
    monkeypatch.setenv("ECO_NO_EI", "0")
    ei.register_magic(shell)
    assert "ei" in shell.magics


# -- the HTTP backend ---------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.headers.get("Authorization"), body))
        self.send_response(200)
        self.end_headers()
        if not body.get("stream"):
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "plain"}}]}).encode())
            return
        deltas = [{"role": "assistant", "content": ""}, {"reasoning": "thinking..."}, {"content": "Hel"}, {"content": "lo"}]
        for d in deltas:
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': d}]})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def test_openai_backend_streams_and_drops_reasoning():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        b = la.OpenAICompatibleBackend(
            f"http://127.0.0.1:{server.server_port}/api/", "m1", api_key="sk-x",
            options={"chat_template_kwargs": {"enable_thinking": False}},
        )
        assert b.models() == ["m1", "m2"]
        pieces = []
        assert b.chat([{"role": "user", "content": "hi"}], on_delta=pieces.append) == "Hello"
        assert pieces == ["Hel", "lo"]
        assert b.complete("hi") == "plain"
        auth, body = _Handler.requests[0]
        assert auth == "Bearer sk-x" and body["stream"] is True
        assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["model"] == "m1"
    finally:
        server.shutdown()


# -- %ei ---------------------------------------------------------------------------


class _Shell:
    def __init__(self, namespace=None):
        self.user_ns = {"namespace": namespace}
        self.next_input = None
        self.magics = {}

    def set_next_input(self, text):
        self.next_input = text

    def register_magic_function(self, fn, magic_kind, magic_name):
        self.magics[magic_name] = (fn, magic_kind)


def test_magic(tmp_path, monkeypatch, capsys):
    pytest.importorskip("IPython")
    import IPython

    shell = _Shell()
    ei.register_magic(shell)
    assert shell.magics["ei"] == (ei.ei_magic, "line_cell")
    monkeypatch.setattr(IPython, "get_ipython", lambda: shell)
    backend = _FakeBackend()
    a = _assistant(tmp_path, backend)
    monkeypatch.setitem(ei._assistants, id(None), a)

    ei.ei_magic("--paste")
    assert "no code" in capsys.readouterr().out
    ei.ei_magic("how do I set the attenuator transmission?")
    out = capsys.readouterr().out
    assert "att.set_transmission(0.1)" in out and "%ei --paste" in out
    ei.ei_magic("--paste")
    assert shell.next_input == "att.set_transmission(0.1)"

    ei.ei_magic("-s mesh scan")
    assert "[doc scanning.md > Mesh]" in capsys.readouterr().out and len(backend.calls) == 1
    ei.ei_magic("-m other-model")
    assert a.model == "other-model"
    ei.ei_magic("-n", cell="a question\nover two lines")
    assert backend.calls[-1][-1]["content"].endswith("Question: a question\nover two lines")
    assert len(backend.calls[-1]) == 2
    ei.ei_magic("--bad att")
    ei.ei_magic("")
    assert "model other-model" in capsys.readouterr().out

    monkeypatch.setattr(ei, "api_key", lambda base_url=None: (None, None))
    a._backend = None
    ei.ei_magic("--key")
    assert "no API key" in capsys.readouterr().out
    ei.ei_magic("")
    out = capsys.readouterr().out
    assert "MISSING -- search only" in out and "Get one at https://ai.psi.ch" in out


# -- notes on paper -------------------------------------------------------------------


def test_to_pil_accepts_camera_frames():
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from eco import logs_sources as ls

    frame = np.linspace(-5.0, 1000.0, 12 * 8).reshape(8, 12)  # float, any range
    img = ls.to_pil(frame)
    assert img.size == (12, 8) and img.mode == "L" and img.getextrema() == (0, 255)
    rgb = ls.to_pil(np.zeros((4, 6, 3), dtype=np.uint8))
    assert rgb.mode == "RGB" and ls.to_pil(rgb) is rgb


def test_vision_transcriber_sends_a_scaled_image():
    pytest.importorskip("PIL")
    from PIL import Image

    from eco import logs_sources as ls

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append((self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "14.10.26 shift notes"}}]}).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        t = ls.VisionTranscriber(f"http://127.0.0.1:{server.server_port}/api", "vl", api_key="sk-x",
                                 max_side=400, options={"chat_template_kwargs": {"enable_thinking": False}})
        assert t(Image.new("RGB", (4000, 3000), "white")) == "14.10.26 shift notes"
    finally:
        server.shutdown()
    auth, body = seen[0]
    assert auth == "Bearer sk-x" and body["model"] == "vl" and body["chat_template_kwargs"]
    text, image = body["messages"][0]["content"]
    assert "verbatim" in text["text"] and "FIGURE:" in text["text"]
    import base64
    import io

    sent = Image.open(io.BytesIO(base64.b64decode(image["image_url"]["url"].split(",", 1)[1])))
    assert sent.size == (400, 300)


def _pdf(tmp_path, with_text):
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 200), "white")
    ImageDraw.Draw(img).text((20, 80), "a scanned page", fill="black")
    if not with_text:
        path = tmp_path / "scan.pdf"
        img.save(path)  # image only: no text layer
        return path
    mpl = pytest.importorskip("matplotlib")
    mpl.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(4, 2))
    fig.text(0.1, 0.5, "29.10.2026 att.set_transmission(0.2) worked")
    path = tmp_path / "typed.pdf"
    fig.savefig(path)
    plt.close(fig)
    return path


def test_pdfs_without_pypdf_use_poppler(tmp_path, monkeypatch):
    import shutil
    import sys

    from eco import logs_sources as ls

    if not (shutil.which("pdftotext") and shutil.which("pdftoppm")):
        pytest.skip("poppler not installed")
    monkeypatch.setitem(sys.modules, "pypdf", None)  # import fails: the fallback is tested
    monkeypatch.setitem(sys.modules, "pypdfium2", None)
    text, note = ls.document_text(_pdf(tmp_path, True))
    assert "att.set_transmission(0.2)" in text and note == ""
    scanned = _pdf(tmp_path, False)
    assert ls.document_text(scanned) == ("", "PDF has no text layer and no OCR configured")
    pages = []
    text, note = ls.document_text(scanned, ocr=lambda img: pages.append(img.size) or "page text")
    assert (text, note) == ("page text", "OCR") and len(pages) == 1


class _FakeTranscriber:
    def __init__(self, text="29.10.2026\nlxt scan, t0 at 1.37 ps\natt.set_transmission(0.05)"):
        self.text, self.images = text, []

    def __call__(self, image):
        self.images.append(image.size)
        return self.text


def test_scan_image_keeps_transcribes_and_indexes(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    a = _assistant(tmp_path, _FakeBackend())
    a.config["scan_dir"] = str(tmp_path / "scans")
    fake = _FakeTranscriber()
    monkeypatch.setattr(a, "transcriber", lambda: fake)
    path, text = a.scan_image(np.zeros((30, 40)), label="cam north", report=None)
    assert path.parent == tmp_path / "scans" and path.name.endswith("_cam_north.png") and path.exists()
    assert (path.parent.stat().st_mode & 0o777) == 0o700
    assert text == fake.text and fake.images == [(40, 30)]
    notes = la.search("t0 lxt", k=5, db_path=a.db_path, kinds=("note",))
    assert notes and "1.37 ps" in notes[0].text
    cmds = la.search("transmission", k=5, db_path=a.db_path, kinds=("note_command",))
    assert [h.text for h in cmds] == ["att.set_transmission(0.05)"]
    assert time_of(cmds[0]) == "2026-10-29"  # dated by what is written on it


def time_of(hit):
    import time as _time

    return _time.strftime("%Y-%m-%d", _time.localtime(hit.t))


def test_scanning_needs_a_model(tmp_path, monkeypatch, capsys):
    pytest.importorskip("IPython")
    import IPython

    monkeypatch.setattr(ei, "api_key", lambda base_url=None: (None, None))
    a = _assistant(tmp_path, None)
    a.config["scan_dir"] = str(tmp_path / "scans")
    with pytest.raises(ei.NoModel):
        a.scan_image(b"not even an image")
    with pytest.raises(ei.NoModel):
        a.scan([tmp_path])
    assert not (tmp_path / "scans").exists()  # nothing written without a model

    shell = _Shell()
    monkeypatch.setattr(IPython, "get_ipython", lambda: shell)
    monkeypatch.setitem(ei._assistants, id(None), a)
    for opt in ("--scan x.png", "--scan-image img", "--webcam"):
        ei.ei_magic(opt)
        assert "needs the model" in capsys.readouterr().out


def test_scan_magic_with_a_model(tmp_path, monkeypatch, capsys):
    pytest.importorskip("IPython")
    pytest.importorskip("PIL")
    import IPython
    from PIL import Image

    a = _assistant(tmp_path, _FakeBackend())
    a.config["scan_dir"] = str(tmp_path / "scans")
    fake = _FakeTranscriber("whiteboard: KB bender drift ~3 um/h")
    monkeypatch.setattr(a, "transcriber", lambda: fake)
    shell = _Shell()
    shell.user_ns["img"] = Image.new("L", (50, 20))
    monkeypatch.setattr(IPython, "get_ipython", lambda: shell)
    monkeypatch.setitem(ei._assistants, id(None), a)

    ei.ei_magic("--scan-image img")
    assert "drift ~3 um/h" in capsys.readouterr().out
    ei.ei_magic("--scan-image nothing")
    assert "no variable 'nothing'" in capsys.readouterr().out

    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (80, 60)).save(photos / "page1.jpg")
    (photos / "typed.txt").write_text("plain notes")
    ei.ei_magic(f"--scan {photos}")
    out = capsys.readouterr().out
    assert "2 files indexed, 1 pictures transcribed" in out and "KB bender" in out
