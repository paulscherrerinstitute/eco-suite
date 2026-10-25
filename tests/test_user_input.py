"""Tests for eco.utilities.user_input: questions from other threads are shown
on the main thread, and unattended (queue) threads take timed defaults at
once."""

import builtins
import threading
import time

import pytest

from eco.utilities import mainthread, user_input


@pytest.fixture
def heartbeat():
    mainthread.uninstall()
    hb = mainthread.install(scope="always")  # no IPython shell under pytest
    yield hb
    mainthread.uninstall()


def in_thread(func):
    result = {}

    def run():
        try:
            result["value"] = func()
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def wait_while_alive(thread, seconds=5):
    t0 = time.monotonic()
    while thread.is_alive() and time.monotonic() - t0 < seconds:
        time.sleep(0.02)  # main thread "busy": the heartbeat runs the dispatch


def test_input_timeout_in_an_unattended_thread_takes_the_default_at_once():
    def asker():
        with user_input.unattended("queue 'test'"):
            t0 = time.monotonic()
            with pytest.raises(user_input.InputTimeout):
                user_input.input_timeout("Change back? (y/n)", timeout=10)
            return time.monotonic() - t0

    thread, result = in_thread(asker)
    thread.join(5)
    assert result["value"] < 0.5


def test_input_timeout_error_is_an_inputimeout_timeout():
    inputimeout = pytest.importorskip("inputimeout")
    assert issubclass(user_input.InputTimeout, inputimeout.TimeoutOccurred)


def test_question_from_a_worker_is_answered_on_the_main_thread(heartbeat, monkeypatch):
    seen = {}

    def fake_channel(question):
        seen["main"] = threading.current_thread() is threading.main_thread()
        seen["origin"] = question.origin
        return "42"

    monkeypatch.setattr(user_input, "_ask_here", fake_channel)

    def asker():
        with user_input.unattended("queue 'test', task #1"):
            return user_input.ask("value?")

    thread, result = in_thread(asker)
    wait_while_alive(thread)
    assert result == {"value": "42"}
    assert seen == {"main": True, "origin": "queue 'test', task #1"}


def test_cancelled_question_raises_in_the_asking_thread(heartbeat, monkeypatch):
    def cancel(question):
        raise user_input.QuestionCancelled(question.prompt)

    monkeypatch.setattr(user_input, "_ask_here", cancel)
    thread, result = in_thread(lambda: user_input.ask("sure?"))
    wait_while_alive(thread)
    assert isinstance(result["error"], user_input.QuestionCancelled)


def test_routed_input_from_a_worker_goes_through_ask(heartbeat, monkeypatch):
    monkeypatch.setattr(user_input, "_ask_here", lambda question: f"answer to {question.prompt}")
    monkeypatch.setattr(builtins, "input", user_input._routed_input)
    thread, result = in_thread(lambda: input("(y/n)"))
    wait_while_alive(thread)
    assert result == {"value": "answer to (y/n)"}


def test_without_any_channel_the_question_waits_for_answer(heartbeat, monkeypatch):
    monkeypatch.setattr(user_input, "_ask_here", lambda question: NotImplemented)
    thread, result = in_thread(lambda: user_input.ask("which edge?"))
    t0 = time.monotonic()
    while not user_input.pending_questions() and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    assert [q.prompt for q in user_input.pending_questions()] == ["which edge?"]
    assert user_input.answer(0.512) is True
    thread.join(5)
    assert result == {"value": "0.512"}
    assert user_input.pending_questions() == []


def test_qt_dialog_channel(heartbeat, monkeypatch):
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    from qtpy import QtCore

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    mainthread.install_qt()
    monkeypatch.setattr(user_input, "_terminal_shell", lambda: None)

    def answer_dialog():
        dialog = QtWidgets.QApplication.activeModalWidget()
        if dialog is None:
            QtCore.QTimer.singleShot(50, answer_dialog)
            return
        dialog.setTextValue("from the dialog")
        dialog.accept()

    QtCore.QTimer.singleShot(200, answer_dialog)
    thread, result = in_thread(lambda: user_input.ask("name?"))
    wait_while_alive(thread)
    assert result == {"value": "from the dialog"}
    del app
