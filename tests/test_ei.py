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
    logs, docs, src = tmp_path / "logs", tmp_path / "docs", tmp_path / "src"
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
    db = tmp_path / "idx.sqlite"
    la.build_index(db_path=db, log_dir=logs, doc_dirs=(docs,), src_dirs=(src,))
    return db


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
