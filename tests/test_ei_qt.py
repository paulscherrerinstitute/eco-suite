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
    def __init__(self, fail=False):
        self.config = {"model": "m1", "model_options": {"m1": {}, "m2": {}}}
        self.fail, self.calls, self.reset_calls = fail, [], 0

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
    while panel._worker is not None and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert panel._worker is None


def test_ask_streams_and_offers_code(app):
    inserted = []
    asst = _FakeAssistant()
    panel = EiPanel(asst, insert_code=inserted.append)
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
