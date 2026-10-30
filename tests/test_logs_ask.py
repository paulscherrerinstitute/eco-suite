"""eco.logs_ask: index build (incremental), search ranking/dedup, ask()
with and without a (fake) model backend. Standard library only."""
import json
import os
import time

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.logs_ask as la


def _log(dir_, name, records):
    p = dir_ / name
    p.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in records) + "\n")
    return p


def _setup(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    _log(logs, "20261020_090000_desktop_aaaa.jsonl", [
        {"t": 1.0, "event": "session_start", "kind": "desktop", "label": "bernina"},
        {"t": 2.0, "event": "input", "code": "att.set_transmission(0.1)"},
        {"t": 2.1, "event": "result", "text": "0.1"},
        {"t": 3.0, "event": "input", "code": "att.set_transmision(0.1)"},
        {"t": 3.1, "event": "error", "ename": "AttributeError", "evalue": "no set_transmision"},
        {"t": 4.0, "event": "input", "code": "att.set_transmission(0.1)"},
        "garbage line",
    ])
    (docs / "scanning.md").write_text("# Scanning\n\n## Mesh scans\n\nUse `s.mesh(...)` for a 2D mesh scan.\n")
    return logs, docs


def test_index_and_search(tmp_path):
    logs, docs = _setup(tmp_path)
    db = tmp_path / "idx.sqlite"
    assert la.build_index(db, log_dir=logs, doc_dirs=[docs], src_dirs=())["files_indexed"] == 2
    hits = la.search("transmission", db_path=db)
    assert [h.text for h in hits if h.kind == "command"] == ["att.set_transmission(0.1)"]  # deduplicated
    assert la.search("mesh scan", db_path=db)[0].kind == "doc"
    failed = [h for h in la.search("set_transmision", db_path=db) if not h.ok]
    assert failed and "AttributeError" in failed[0].context


def test_index_is_incremental(tmp_path):
    logs, docs = _setup(tmp_path)
    db = tmp_path / "idx.sqlite"
    la.build_index(db, log_dir=logs, doc_dirs=[docs], src_dirs=())
    assert la.build_index(db, log_dir=logs, doc_dirs=[docs], src_dirs=())["files_indexed"] == 0
    p = next(logs.glob("*.jsonl"))
    with open(p, "a") as f:
        f.write(json.dumps({"t": 9.0, "event": "input", "code": "mono.mv(8000)"}) + "\n")
    r = la.build_index(db, log_dir=logs, doc_dirs=[docs], src_dirs=())
    assert r["files_indexed"] == 1
    assert la.search("mono", db_path=db)[0].text == "mono.mv(8000)"


def test_query_syntax_never_raises(tmp_path):
    logs, docs = _setup(tmp_path)
    db = tmp_path / "idx.sqlite"
    la.build_index(db, log_dir=logs, doc_dirs=[docs], src_dirs=())
    for q in ['"', "AND OR NOT", "a(b", "*", "", "att.set_transmission("]:
        la.search(q, db_path=db)


class _FakeBackend:
    def __init__(self):
        self.prompt = None

    def complete(self, prompt):
        self.prompt = prompt
        return "Use att.set_transmission(0.1)."


def test_ask_with_and_without_backend(tmp_path):
    logs, docs = _setup(tmp_path)
    db = tmp_path / "idx.sqlite"
    la.build_index(db, log_dir=logs, doc_dirs=[docs], src_dirs=())
    plain = la.ask("how to set the transmission", db_path=db)
    assert "att.set_transmission(0.1)" in plain
    fake = _FakeBackend()
    answer = la.ask("how to set the transmission", backend=fake, db_path=db)
    assert answer.startswith("Use att.set_transmission(0.1).")
    assert "based on:" in answer
    assert "ONLY the context" in fake.prompt and "att.set_transmission(0.1)" in fake.prompt
    assert la.ask("zzzzunknown", db_path=db).startswith("Nothing")
