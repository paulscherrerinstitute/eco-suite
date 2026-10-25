"""Tests for eco.utilities.task_queue: named queues of statements/callables
on a worker thread, their test run, and the %queue magic's parsing."""

import threading
import time

import pytest

from eco.utilities import mainthread, task_queue, user_input
from eco.utilities.task_queue import QueueTestError, TaskQueue


@pytest.fixture
def ns():
    return {"__builtins__": __builtins__}


@pytest.fixture
def queue(ns):
    q = TaskQueue("test", namespace=ns)
    yield q
    q.stop()
    q.close()


def finish(q, timeout=5):
    q.wait(timeout=timeout)


def test_statements_run_in_order_in_the_shared_namespace(queue, ns):
    ns["log"] = []
    queue.append("log.append(1)")
    queue.append("x = 41")
    queue.append("log.append(x + 1)")
    finish(queue)
    assert ns["log"] == [1, 42]
    assert ns["x"] == 41
    assert [t.state for t in queue.history] == ["done", "done", "done"]


def test_a_variable_changed_meanwhile_is_seen_when_the_task_runs(queue, ns):
    ns["gate"] = threading.Event()
    ns["start"] = 0
    queue.append("gate.wait(5)")
    queue.append("seen = start")
    ns["start"] = 7  # changed "at the prompt" before the task runs
    ns["gate"].set()
    finish(queue)
    assert ns["seen"] == 7


def test_lone_expression_result_is_kept(queue):
    task = queue.append("6 * 7")
    assert task.wait(5) == 42


def test_callable_tasks(queue):
    calls = []
    task = queue.append(calls.append, "hello")
    task.wait(5)
    assert calls == ["hello"]


def test_typo_in_a_name_is_rejected_with_a_suggestion(queue, ns):
    ns["scans"] = object()
    with pytest.raises(QueueTestError) as info:
        queue.append("scnas.ascan(1, 2)")
    assert "did you mean 'scans'" in str(info.value)
    assert queue.pending == []


def test_typo_in_an_attribute_is_rejected(queue, ns):
    class Mono:
        energy = None

    ns["mono"] = Mono()
    with pytest.raises(QueueTestError) as info:
        queue.append("mono.enrgy")
    assert "did you mean 'energy'" in str(info.value)


def test_wrong_arguments_are_rejected(queue, ns):
    ns["f"] = lambda a, b: None
    with pytest.raises(QueueTestError) as info:
        queue.append("f(1, 2, 3)")
    assert "too many positional arguments" in str(info.value)


def test_names_assigned_by_earlier_tasks_count_as_defined(queue, ns):
    queue.pause()
    queue.append("edge = 0.5")
    queue.append("result = edge * 2")  # `edge` does not exist yet, but will
    queue.resume()
    finish(queue)
    assert ns["result"] == 1.0


def test_properties_are_not_evaluated_by_the_test(queue, ns):
    reads = []

    class Device:
        @property
        def value(self):
            reads.append(1)
            return 1

    ns["dev"] = Device()
    queue.pause()
    queue.append("v = dev.value")
    assert reads == []


def test_move_outside_limits_is_rejected(queue, ns):
    class Motor:
        def get_limits(self):
            return (-1, 1)

        def get_current_value(self):
            return 0.5

        def mv(self, value):
            pass

        def mvr(self, value):
            pass

    ns["m"] = Motor()
    with pytest.raises(QueueTestError):
        queue.append("m.mv(5)")
    with pytest.raises(QueueTestError):
        queue.append("m.mvr(0.8)")  # 0.5 + 0.8 > 1
    queue.pause()
    queue.append("m.mv(0.2)")


def test_test_run_reports_pending_problems(queue, ns):
    queue.pause()
    queue.append("y = 1")
    queue.append("missing_name + 1", test=False)
    report = queue.test_run(verbose=False)
    assert list(report) == [queue.pending[1]]


def test_failure_pauses_the_queue(queue, ns):
    ns["log"] = []
    queue.append("1 / 0")
    queue.append("log.append('after')")
    time.sleep(0.5)
    assert queue.paused
    assert queue.history[0].state == "error"
    assert ns["log"] == []
    queue.resume()
    finish(queue)
    assert ns["log"] == ["after"]


def test_pause_cancel_and_repeat_last(queue, ns):
    ns["gate"] = threading.Event()
    ns["log"] = []
    queue.append("gate.wait(5)")
    queue.append("log.append('a')")
    queue.append("log.append('b')")
    time.sleep(0.2)
    dropped = queue.cancel_after_running_task()
    assert len(dropped) == 2
    queue.pause_after_running_task()
    queue.append("log.append('c')")
    queue.repeat_last(N=2)
    ns["gate"].set()
    time.sleep(0.3)
    assert ns["log"] == []  # paused after the running task
    queue.resume()
    finish(queue)
    assert ns["log"] == ["c", "c", "c"]


def test_stop_running_task_interrupts_plain_python(queue, ns):
    task = queue.append("while True: pass")
    time.sleep(0.3)
    queue.stop_running_task()
    with pytest.raises(KeyboardInterrupt):
        task.wait(5)
    assert queue.paused


def test_tasks_run_unattended_and_know_their_task(queue, ns):
    ns["task_queue"] = task_queue
    ns["user_input"] = user_input
    task = queue.append("(task_queue.current_task().id, user_input.unattended_label())")
    task_id, label = task.wait(5)
    assert task_id == task.id
    assert label == task.label


def test_input_timeout_in_a_task_takes_the_default_at_once(queue, ns):
    ns["user_input"] = user_input
    queue.append(
        "t0 = __import__('time').monotonic()\n"
        "try:\n"
        "    user_input.input_timeout('go on? (y/n)', timeout=10)\n"
        "except user_input.InputTimeout:\n"
        "    took = __import__('time').monotonic() - t0"
    )
    finish(queue)
    assert ns["took"] < 0.5


def test_input_in_a_task_is_asked_on_the_main_thread(queue, ns, monkeypatch):
    mainthread.uninstall()
    mainthread.install(scope="always")
    try:
        monkeypatch.setattr(user_input, "_ask_here", lambda question: f"{question.origin}: yes")
        ns["user_input"] = user_input
        task = queue.append("answer = user_input.ask('sure?')")
        t0 = time.monotonic()
        while task.state != "done" and time.monotonic() - t0 < 5:
            time.sleep(0.02)
        assert ns["answer"] == f"{task.label}: yes"
    finally:
        mainthread.uninstall()


def test_description_of_a_running_task_reaches_its_scans(queue):
    class FakeScan:
        description = None

        def set_description(self, text):
            self.description = text

    task = task_queue.Task("pass")
    scan = FakeScan()
    task.scans.append(scan)
    task.description = "new title"
    assert scan.description == "new title"


def test_magic_appends_and_runs_subcommands(monkeypatch):
    q = TaskQueue("magic", namespace={"__builtins__": __builtins__})
    monkeypatch.setitem(task_queue.queues, "magic", q)
    try:
        q.pause()
        task_queue.queue_magic("-q magic x = 1")
        task_queue.queue_magic("-q magic repeat_last 2")
        assert [t.source for t in q.pending] == ["x = 1"] * 3
        task_queue.queue_magic("-q magic cancel_after_running_task")
        assert q.pending == []
        task_queue.queue_magic("-q magic -- status = 3")  # a statement, not the subcommand
        assert [t.source for t in q.pending] == ["status = 3"]
    finally:
        q.stop()
        q.close()


def test_magic_reports_a_failed_test_instead_of_raising(monkeypatch, capsys):
    q = TaskQueue("magic2", namespace={"__builtins__": __builtins__})
    monkeypatch.setitem(task_queue.queues, "magic2", q)
    try:
        task_queue.queue_magic("-q magic2 undefined_thing()")
        assert "not queued" in capsys.readouterr().out
        assert q.pending == []
    finally:
        q.close()


# -- scans in queues ------------------------------------------------------------


@pytest.fixture
def scans_setup():
    """A real Scans instance with in-memory adjustables and a fake counter --
    nothing touches hardware or the filesystem."""
    from eco.acquisition.scan import Scans
    from eco.acquisition.utilities import Acquisition
    from eco.elements.adjustable import DummyAdjustable

    class FakeCounter:
        name = "fake"

        def __init__(self):
            self.threads = []

        def acquire(self, scan=None, Npulses=None, **kwargs):
            self.threads.append(threading.current_thread().name)
            return Acquisition(acquire=lambda: time.sleep(0.01), hold=False)

    counter = FakeCounter()
    scans = Scans(default_counters=[counter], name="scans")
    mot = DummyAdjustable(name="mot", limits=[-2, 2])
    mot2 = DummyAdjustable(name="mot2", limits=[-2, 2])
    return scans, mot, mot2, counter


def test_scans_queue_keyword_appends_and_runs_on_the_worker(scans_setup, monkeypatch):
    scans, mot, _, counter = scans_setup
    q = TaskQueue("scan-test", namespace={"__builtins__": __builtins__})
    monkeypatch.setitem(task_queue.queues, "scan-test", q)
    try:
        task = scans.ascan(mot, 0, 1, 2, 1, queue="scan-test", return_at_end=False)
        assert isinstance(task, task_queue.Task)
        scan = task.wait(20)
        assert scan.adjustables == [mot]
        assert task.scan is scan
        assert set(counter.threads) == {"eco-queue-scan-test"}
        assert mot.get_current_value() == 1
    finally:
        q.stop()
        q.close()


def test_test_building_a_scan_moves_nothing_and_keeps_acquiring_scan(scans_setup, monkeypatch):
    scans, mot, _, counter = scans_setup
    before = scans.acquiring_scan
    q = TaskQueue("scan-test2", namespace={"__builtins__": __builtins__, "scans": scans, "mot": mot})
    monkeypatch.setitem(task_queue.queues, "scan-test2", q)
    try:
        q.pause()
        q.append("scans.ascan(mot, 0, 1, 2, 1)")
        assert counter.threads == []  # nothing acquired
        assert mot.get_current_value() == 0  # nothing moved
        assert scans.acquiring_scan is before
        with pytest.raises(QueueTestError) as info:
            q.append("scans.ascan(mot, 0, 5, 2, 1)")
        assert "outside its limits (-2, 2)" in str(info.value)
    finally:
        q.stop()
        q.close()


def test_a2scan_can_be_test_built(scans_setup):
    scans, mot, mot2, _ = scans_setup
    scan = scans.a2scan(mot, 0, 1, mot2, 0, 3, 2, 1, start_immediately=False)
    from eco.acquisition.scan_tasks import check_scan_limits

    problems = check_scan_limits(scan)
    assert len(problems) == 1 and "mot2" in problems[0]


def test_queue_output_goes_to_the_configured_stream(queue, ns, monkeypatch):
    import io
    import sys

    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(task_queue, "_output", {"stdout": None, "stderr": None})
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    task_queue.send_output_to(console)
    try:
        queue.append("print('from the queue')")
        finish(queue)
        print("from the main thread")
        assert console.getvalue() == "from the queue\n"
    finally:
        sys.stdout = sys.stdout._fallback
        sys.stderr = sys.stderr._fallback


# -- claims: what a running task moves can't be moved from elsewhere ----------------


def test_a_tasks_motor_cannot_be_moved_from_the_prompt_unless_forced(queue, ns):
    from eco.elements.adjustable import DummyAdjustable

    mot = DummyAdjustable(name="mot")
    ns["mot"] = mot
    ns["gate"] = threading.Event()
    task = queue.append("mot.set_target_value(1).wait(); gate.wait(5)")
    t0 = time.monotonic()
    while mot.get_current_value() != 1 and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    with pytest.raises(task_queue.MoveBlocked) as info:
        mot.set_target_value(5).wait()
    message = str(info.value)
    assert f"in use by {task.label}" in message and "with forced():" in message
    assert mot.get_current_value() == 1  # not moved
    assert set(task_queue.claims()) == {"mot"}
    with task_queue.forced():
        mot.set_target_value(0.5).wait()
    assert mot.get_current_value() == 0.5
    ns["gate"].set()
    task.wait(5)
    mot.set_target_value(2).wait()  # the claim ended with the task
    assert task_queue.claims() == {}


def test_the_tasks_own_moves_through_a_virtual_adjustable_are_not_blocked(queue, ns):
    from eco.elements.adjustable import AdjustableVirtual, DummyAdjustable

    a, b = DummyAdjustable(name="a"), DummyAdjustable(name="b")
    both = AdjustableVirtual(
        [a, b], lambda va, vb: va, lambda v: (v, 2 * v), name="both"
    )
    ns.update(both=both, gate=threading.Event())
    task = queue.append("both.set_target_value(1).wait(); both.set_target_value(2).wait(); gate.wait(5)")
    t0 = time.monotonic()
    while b.get_current_value() != 4 and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    assert (a.get_current_value(), b.get_current_value()) == (2, 4)
    with pytest.raises(task_queue.MoveBlocked):
        b.set_target_value(0).wait()  # the motor underneath is claimed too
    ns["gate"].set()
    task.wait(5)
    assert task.state == "done"


def test_a_queued_scan_claims_its_adjustables_from_its_start(scans_setup, monkeypatch):
    scans, mot, _, counter = scans_setup
    slow = threading.Event()
    counter.acquire = lambda scan=None, Npulses=None, **kw: __import__(
        "eco.acquisition.utilities", fromlist=["Acquisition"]
    ).Acquisition(acquire=lambda: slow.wait(5), hold=False)
    q = TaskQueue("claim-scan", namespace={"__builtins__": __builtins__})
    monkeypatch.setitem(task_queue.queues, "claim-scan", q)
    try:
        task = scans.ascan(mot, 0, 1, 2, 1, queue="claim-scan", return_at_end=False)
        t0 = time.monotonic()
        while task.scan is None and time.monotonic() - t0 < 10:
            time.sleep(0.02)
        with pytest.raises(task_queue.MoveBlocked):
            mot.set_target_value(1.5).wait()
        assert q.release(mot) is True
        slow.set()
        task.wait(20)
    finally:
        slow.set()
        q.stop()
        q.close()


# -- output: captured per task, mutable, recorded, shown live ----------------------


@pytest.fixture
def session_out(monkeypatch):
    """sys.stdout as the session sees it, with the queue's router in front."""
    import io
    import sys

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", out)
    task_queue._install_output_router()
    return out


def test_task_output_is_captured_including_threads_it_starts(queue, ns, session_out):
    from eco.utilities.utilities import PropagatingThread

    ns["PropagatingThread"] = PropagatingThread
    task = queue.append(
        "print('from the task')\n"
        "t = PropagatingThread(target=print, args=('from its thread',)); t.start(); t.join()"
    )
    task.wait(5)
    assert task.output == "from the task\nfrom its thread\n"
    assert "from the task" in session_out.getvalue()  # not muted: shown too


def test_muted_output_is_kept_but_failures_still_shown(queue, ns, session_out):
    queue.mute()
    task = queue.append("print('chatty')")
    task.wait(5)
    failing = queue.append("print('before'); 1 / 0")
    with pytest.raises(ZeroDivisionError):
        failing.wait(5)
    shown = session_out.getvalue()
    assert "chatty" not in shown and "before" not in shown
    assert "failed: ZeroDivisionError" in shown
    assert task.output == "chatty\n"
    assert "failed: ZeroDivisionError" in failing.output


def test_record_writes_headers_and_output(queue, ns, session_out, tmp_path):
    path = queue.record(tmp_path / "queue.log")
    queue.append("print('step 1 of 2')").wait(5)
    queue.stop_recording()
    text = path.read_text()
    assert "=== queue 'test', task #" in text and "started" in text
    assert "step 1 of 2\n" in text
    assert "done after" in text


def test_output_window_shows_earlier_and_new_output(queue, ns, session_out):
    QtWidgets = pytest.importorskip("qtpy.QtWidgets")
    from qtpy import QtCore

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    queue.append("print('earlier')").wait(5)
    from eco.widgets.queue_output_qt import QueueOutputQt

    window = QueueOutputQt(queue)
    try:
        queue.append("print('later')").wait(5)
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(500, loop.quit)
        loop.exec_()
        text = window._text.toPlainText()
        assert "earlier" in text and "later" in text
        assert "queue 'test'" in window._status.text()
    finally:
        window.stop()
    del app
