"""The Ask panel (eco.widgets.ei_qt): a question runs on a worker thread,
the streamed answer and the result arrive through queued signals, and its
code goes to the console only on request."""
import time

import pytest

pytest.importorskip("qtpy")
from qtpy import QtWidgets

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.ei as ei
from eco.widgets.ei_qt import EiPanel


class _FakeAssistant:
    def __init__(self, fail=False, key=True):
        self.config = {"model": "m1", "model_options": {"m1": {}, "m2": {}}}
        self.fail, self.calls, self.reset_calls, self.key = fail, [], 0, key

    def backend(self, recheck=False):
        return object() if self.key else None

    @property
    def model(self):
        return self.config["model"]

    def set_model(self, model):
        self.config["model"] = model

    def reset(self):
        self.reset_calls += 1

    def ask(self, question, on_delta=None, stop=None, search_only=False, report=None):
        self.calls.append((question, search_only))
        if self.fail:
            raise ConnectionError("no route")
        text = "Do this:\n\n```python\natt.set_transmission(0.1)\n```\n"
        if not search_only:
            report("searching")
            on_delta(text[:6])
            on_delta(text[6:])
        return ei.Answer(question, text, [], "" if search_only else self.model, 0.5)


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wait(app, panel, timeout=5):
    end = time.time() + timeout
    while (panel._worker is not None or panel._has_key is None) and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert panel._worker is None


def test_ask_streams_and_offers_code(app):
    inserted = []
    asst = _FakeAssistant()
    panel = EiPanel(asst, insert_code=inserted.append)
    _wait(app, panel)
    assert panel._has_key is True and panel.search_only.isEnabled()
    assert [panel.model_box.itemText(i) for i in range(panel.model_box.count())] == ["m1", "m2"]
    assert not panel.console_button.isEnabled()

    panel.question.setPlainText("set the attenuator")
    panel.ask()
    assert not panel.ask_button.isEnabled()
    _wait(app, panel)
    assert asst.calls == [("set the attenuator", False)]
    assert "att.set_transmission(0.1)" in panel.answer.toPlainText()
    assert panel.status.text().startswith("m1, 0.5 s")
    assert panel.console_button.isEnabled() and panel.ask_button.isEnabled()
    panel.console_button.click()
    assert inserted == ["att.set_transmission(0.1)"]

    panel.model_box.setCurrentIndex(1)
    panel.model_box.activated.emit(1)
    assert asst.model == "m2"
    panel.new_button.click()
    assert asst.reset_calls == 1 and not panel.console_button.isEnabled()
    panel.deleteLater()


def test_search_only_and_failure(app):
    asst = _FakeAssistant(fail=True)
    panel = EiPanel(asst)
    assert not panel.console_button.isVisible()
    panel.question.setPlainText("x")
    panel.ask()
    _wait(app, panel)
    assert panel.status.text() == "failed: ConnectionError: no route"
    assert panel.ask_button.isEnabled()

    asst.fail = False
    panel.search_only.setChecked(True)
    panel.ask()
    _wait(app, panel)
    assert asst.calls[-1] == ("x", True)
    assert panel.status.text() == "0 matches (no model asked)"
    panel.deleteLater()


def test_closing_mid_answer_is_harmless(app):
    asst = _FakeAssistant()
    panel = EiPanel(asst)
    panel.question.setPlainText("x")
    panel.ask()
    worker = panel._worker
    panel.close()
    panel.deleteLater()
    app.processEvents()
    worker.join(5)
    for _ in range(5):
        app.processEvents()
    assert not worker.is_alive()


def test_without_a_key_the_panel_only_searches(app):
    asst = _FakeAssistant(key=False)
    panel = EiPanel(asst)
    _wait(app, panel)
    assert panel.search_only.isChecked() and not panel.search_only.isEnabled()
    assert not panel.model_box.isEnabled()
    assert "No API key" in panel.status.text()
    panel.question.setPlainText("x")
    panel.ask()
    _wait(app, panel)
    assert asst.calls == [("x", True)] and not panel.model_box.isEnabled()
    panel.deleteLater()


def test_scan_buttons_need_the_model(app):
    panel = EiPanel(_FakeAssistant(key=False))
    _wait(app, panel)
    assert not panel.scan_button.isEnabled() and not panel.webcam_button.isEnabled()
    panel.scan_files(["x.png"])  # ignored without a model
    assert not panel._scanning
    panel.deleteLater()


def test_scan_files_shows_the_transcripts(app):
    asst = _FakeAssistant()
    asst.scan = lambda paths, report=None: {
        "files_indexed": len(paths), "items": 3, "notes": {"b.pdf": "scanned PDF needs pypdfium2 or pdftoppm for OCR"},
        "transcripts": ["page one text"],
    }
    panel = EiPanel(asst)
    _wait(app, panel)
    assert panel.scan_button.isEnabled() and panel.webcam_button.isEnabled()
    panel.scan_files(["a.png", "b.pdf"])
    end = time.time() + 5
    while panel._scanning and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert "page one text" in panel.answer.toPlainText() and "b.pdf: scanned PDF" in panel.answer.toPlainText()
    assert panel.status.text() == "2 file(s) indexed, 1 picture(s) transcribed"
    panel.deleteLater()


def test_webcam_dialog_without_camera_and_a_capture(app, monkeypatch):
    pytest.importorskip("PIL")
    from qtpy import QtGui

    from eco.widgets.ei_qt import WebcamDialog

    calls = []
    asst = _FakeAssistant()
    asst.scan_image = lambda image, label="image", report=None: calls.append((image.size, label)) or ("/x/w.png", "a note")
    dialog = WebcamDialog(asst)
    dialog.cameras = []  # whatever the test machine has
    if dialog._mm is not None:
        assert "camera" in dialog.status.text().lower() or dialog.capture is not None
    # what a capture delivers
    qimage = QtGui.QImage(64, 48, QtGui.QImage.Format_RGB32)
    qimage.fill(QtGui.QColor("white"))
    dialog._busy = True
    dialog._on_captured(1, qimage)
    end = time.time() + 5
    while dialog._busy and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert calls == [((64, 48), "webcam")]
    assert dialog.transcript.toPlainText() == "a note" and "Indexed: /x/w.png" in dialog.status.text()
    dialog.done(0)
    dialog.deleteLater()
