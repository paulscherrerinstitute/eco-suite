"""eco.logs_sources (dates in free text, notes, PDFs, OCR hook, scilog) and
the parts of eco.logs_ask built on them: warnings as a command outcome,
command_outcomes(), index_documents(), index_scilog(). No network, no OCR
engine: scilog and OCR are faked."""
import datetime
import json
import warnings

import pytest

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.logs_ask as la
from eco import logs_sources as ls
from eco.widgets import kernel_registry


def _ts(y, m, d, hh=0, mm=0):
    return datetime.datetime(y, m, d, hh, mm).timestamp()


# -- dates --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Beamtime 2026-10-29", _ts(2026, 10, 29)),
        ("2026-10-29 14:05 aligned", _ts(2026, 10, 29, 14, 5)),
        ("29.10.2026", _ts(2026, 10, 29)),
        ("29.10.26 night shift", _ts(2026, 10, 29)),
        ("01.02.2026", _ts(2026, 2, 1)),  # day first
        ("29/10/2026", _ts(2026, 10, 29)),
        ("29 Oct 2026", _ts(2026, 10, 29)),
        ("29. Oktober 2026", _ts(2026, 10, 29)),
        ("3. März 2026", _ts(2026, 3, 3)),
        ("October 29, 2026", _ts(2026, 10, 29)),
    ],
)
def test_find_dates_formats(text, expected):
    dates = ls.find_dates(text)
    assert len(dates) == 1 and dates[0][1] == expected


def test_find_dates_ignores_impossible_and_versions():
    assert ls.find_dates("31.02.2026") == []
    assert ls.find_dates("version 1.2.3, pulse 12.5") == []


def test_dated_chunks_carry_the_date_downwards():
    text = "29.10.2026\n\nmono at 8 keV\n\nscan done\n\n30.10.2026\n\nnext day"
    chunks = ls.dated_chunks(text, default_t=0.0)
    assert [t for t, _ in chunks] == [_ts(2026, 10, 29), _ts(2026, 10, 30)]
    assert "scan done" in chunks[0][1] and "next day" in chunks[1][1]
    assert ls.dated_chunks("undated text", default_t=5.0) == [(5.0, "undated text")]


def test_command_lines():
    text = """Aligned the sample.
>>> mono.mv(8000)
In [12]: att.set_transmission(0.1)
s.ascan(mot, 0, 1, 10, .3)
mono.energy = 8000
Energy = 8 keV
then it worked (finally)."""
    assert ls.command_lines(text) == [
        "mono.mv(8000)", "att.set_transmission(0.1)", "s.ascan(mot, 0, 1, 10, .3)", "mono.energy = 8000",
    ]


def test_html_to_text():
    assert ls.html_to_text("<p>moved <b>mono</b></p><p>mono.mv(1)</p>") == "moved mono\n\nmono.mv(1)"


# -- documents -----------------------------------------------------------------------


def test_document_text_txt_and_unsupported(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    assert ls.document_text(tmp_path / "a.txt") == ("hello", "")
    (tmp_path / "a.xyz").write_text("x")
    assert ls.document_text(tmp_path / "a.xyz")[0] == ""


def test_document_text_pdf(tmp_path):
    pytest.importorskip("pypdf")
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    p = tmp_path / "log.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(72, 750, "29.10.2026")
    c.drawString(72, 730, "att.set_transmission(0.2)")
    c.save()
    text, note = ls.document_text(p)
    assert "29.10.2026" in text and "att.set_transmission(0.2)" in text and note == ""


def test_document_text_image_needs_ocr(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    p = tmp_path / "scan.png"
    Image.new("RGB", (10, 10)).save(p)
    assert ls.document_text(p) == ("", "image and no OCR configured")
    text, note = ls.document_text(p, ocr=lambda img: "29.10.2026\nmono.mv(7000)")
    assert note == "OCR" and "mono.mv(7000)" in text


def test_index_documents_dates_notes_and_their_commands(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "beamtime.md").write_text(
        "29.10.2026\n\nTransmission too high, reduced it:\n>>> att.set_transmission(0.05)\n\n"
        "30.10.2026\n\nchanged energy\nmono.mv(9000)\n"
    )
    db = tmp_path / "idx.sqlite"
    r = la.index_documents(notes, db_path=db)
    assert r["files_indexed"] == 1
    hits = la.search("transmission", db_path=db, kinds=("note_command",))
    assert hits[0].text == "att.set_transmission(0.05)" and hits[0].t == _ts(2026, 10, 29)
    mono = la.search("mono", db_path=db, kinds=("note_command",))[0]
    assert mono.t == _ts(2026, 10, 30)
    # date window
    assert la.search("mono", db_path=db, until=_ts(2026, 10, 29, 23, 59)) == []
    # incremental
    assert la.index_documents(notes, db_path=db)["files_indexed"] == 0


def test_index_documents_reports_skipped_files(tmp_path):
    pytest.importorskip("PIL.Image")
    from PIL import Image

    Image.new("RGB", (5, 5)).save(tmp_path / "photo.jpg")
    r = la.index_documents(tmp_path, db_path=tmp_path / "idx.sqlite")
    assert r["files_indexed"] == 0 and "OCR" in next(iter(r["notes"].values()))


# -- scilog ----------------------------------------------------------------------------


class _Snip:
    def __init__(self, i, created, text, tags=()):
        self.id, self.createdAt, self.textcontent = f"id{i}", created, text
        self.createdBy, self.tags = "henrik", list(tags)


class _FakeScilog:
    def __init__(self, snippets):
        self.snippets, self.calls = snippets, []

    def get_snippets(self, where=None, order=None, limit=None, **kw):
        self.calls.append(where)
        after = (where or {}).get("createdAt", {}).get("gt")
        return [s for s in self.snippets if after is None or s.createdAt > after][:limit]


def test_index_scilog_incremental(tmp_path):
    db = tmp_path / "idx.sqlite"
    fake = _FakeScilog([
        _Snip(1, "2026-10-29T10:00:00Z", "<p>Detector timing scan</p><p>s.ascan(tim, 0, 5, 50, .1)</p>", ["scan"]),
        _Snip(2, "2026-10-29T11:00:00Z", "<p>beam lost, waiting</p>"),
    ])
    assert la.index_scilog("p12345", db_path=db, client=fake)["new_posts"] == 2
    hits = la.search("timing", db_path=db, kinds=("scilog",))
    assert hits and "Detector timing scan" in hits[0].text and "henrik" in hits[0].context
    assert la.search("ascan", db_path=db, kinds=("note_command",))[0].text == "s.ascan(tim, 0, 5, 50, .1)"
    assert la.index_scilog("p12345", db_path=db, client=fake)["new_posts"] == 0
    assert fake.calls[-1]["createdAt"] == {"gt": "2026-10-29T11:00:00Z"}


# -- warnings as outcomes ------------------------------------------------------------


def _log(dir_, name, records):
    (dir_ / name).write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_warnings_mark_commands_and_command_outcomes(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _log(logs, "20261029_090000_desktop_aaaa.jsonl", [
        {"t": 1.0, "event": "session_start", "kind": "desktop", "label": "bernina"},
        {"t": 2.0, "event": "input", "code": "mono.mv(8000)"},
        {"t": 2.5, "event": "stream", "name": "stdout", "text": "moving...\ndone\n"},
        {"t": 3.0, "event": "input", "code": "mono.mv(9000)"},
        {"t": 3.5, "event": "stream", "name": "stderr", "text": "WARNING: soft limit reached on mono\n"},
        {"t": 4.0, "event": "input", "code": "daq.acquire('x')"},
        {"t": 4.1, "event": "warning", "category": "UserWarning", "text": "detector JF not connected"},
        {"t": 5.0, "event": "input", "code": "daq.acquire('y')"},
        {"t": 5.1, "event": "error", "ename": "TimeoutError", "evalue": "no answer"},
        {"t": 6.0, "event": "input", "code": "att.set_transmission(0.1)"},
        {"t": 6.1, "event": "result", "text": "0.1"},
    ])
    db = tmp_path / "idx.sqlite"
    la.build_index(db, log_dir=logs, doc_dirs=[], src_dirs=())
    status = {h.text: (h.status, h.flags) for h in la.search("mono daq att", k=20, db_path=db, kinds=("command",))}
    assert status["mono.mv(8000)"] == ("ok", "")
    assert status["mono.mv(9000)"][0] == "warning" and "soft limit" in status["mono.mv(9000)"][1]
    assert status["daq.acquire('x')"] == ("warning", "UserWarning: detector JF not connected")
    assert status["daq.acquire('y')"][0] == "error"
    assert status["att.set_transmission(0.1)"] == ("ok", "")

    out = {r["command"]: r for r in la.command_outcomes(db_path=db)}
    assert out["daq.acquire('…')"]["runs"] == 2
    assert out["daq.acquire('…')"]["warning"] == 1 and out["daq.acquire('…')"]["error"] == 1
    assert out["mono.mv(#)"] ["runs"] == 2 and out["mono.mv(#)"]["warning"] == 1
    assert la.command_outcomes(db_path=db)[0]["command"] == "daq.acquire('…')"  # worst first

    answer = la.ask("daq acquire", db_path=db)
    assert "(warning: UserWarning: detector JF not connected)" in answer
    assert "(failed: TimeoutError: no answer)" in answer


def test_command_shape():
    assert la.command_shape("att.set_transmission(0.36)") == "att.set_transmission(#)"
    assert la.command_shape("s.ascan(m1, -1, 1e-3, 10)") == "s.ascan(m1, #, #, #)"
    assert la.command_shape("daq.acquire(\"run_5\")") == "daq.acquire('…')"


def test_install_warning_capture_logs_and_still_shows(tmp_path):
    session = kernel_registry.KernelSession("console", log_dir=tmp_path)
    shown = []
    saved = warnings.showwarning
    try:
        warnings.showwarning = lambda *a, **k: shown.append(a[0])
        kernel_registry.install_warning_capture(session)
        kernel_registry.install_warning_capture(session)  # idempotent
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("PV not connected", UserWarning)
    finally:
        warnings.showwarning = saved
    recs = [r for r in session.tail(10) if r["event"] == "warning"]
    assert len(recs) == 1 and recs[0]["text"] == "PV not connected" and recs[0]["category"] == "UserWarning"
    assert len(shown) == 1

