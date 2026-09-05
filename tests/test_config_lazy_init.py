import pytest

from eco.utilities.config import Component, Namespace


class BadThing:
    def __init__(self, value, name=None):
        raise ValueError("boom")


class DependencyThing:
    def __init__(self, name=None):
        self.name = name


class NeedsDependency:
    def __init__(self, dependency, name=None):
        self.dependency = dependency


def test_lazy_init_failure_includes_manual_instantiation_string():
    ns = Namespace(name="test")
    ns.append_obj(BadThing, 1, lazy=True, name="bad")

    with pytest.raises(ValueError) as exc_info:
        ns.init_name("bad", raise_errors=True)

    assert exc_info.value.args[0] == "boom"

    manual = exc_info.value.args[-1]
    assert manual.startswith("Manual instantiation attempt:")
    assert "from test_config_lazy_init import BadThing" in manual
    assert manual == (
        "Manual instantiation attempt: "
        "from test_config_lazy_init import BadThing; "
        "BadThing(1, name='bad')"
    )
    assert ns.failed_items_excpetion["bad"].args[-1] == manual


def test_append_obj_from_config_resolves_component_dependencies_eagerly_for_non_lazy_nodes():
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")

    ns.append_obj_from_config(
        {
            "type": f"{__name__}:NeedsDependency",
            "name": "needs_dependency",
            "args": [Component("dependency")],
            "kwargs": {},
            "lazy": False,
        }
    )

    root = ns.initialized_items["needs_dependency"]
    assert isinstance(root.dependency, DependencyThing)
    assert root.dependency is not ns.get_obj("dependency")
    assert root.dependency.name == "dependency"


def test_init_all_retry_loop_is_bounded_by_n_cycles():
    """A name that reports "already initializing" on every attempt used to
    be retried for ever: the loop only stops when the set of such names is
    empty, and a build slower than init_timeout re-raises it every round.
    Observed live as a status-server init sitting at 76/87 for >10 minutes.
    """
    from eco.utilities.config import IsInitialisingError

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="stuck")

    calls = []

    def always_initialising(name, verbose=True, raise_errors=False, quiet=False):
        calls.append(name)
        raise IsInitialisingError(f"{name} is being initialized elsewhere")

    ns.init_name = always_initialising
    ns.init_all(
        required_only=False,
        background=False,
        silent=True,
        N_cycles=3,
        giveup_failed=False,
    )

    assert calls == ["stuck"] * 3


def test_resolve_item_does_not_build_a_lazy_item():
    """`lazy_items.get(n) or failed_items.get(n)` evaluated the proxy's
    truthiness, which resolves it - so merely looking a name up built the
    device."""
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")

    item = ns.resolve_item("dependency")

    assert item is not None
    assert ns.initialized_names == set(), "looking the name up initialized it"
    assert "dependency" in ns.lazy_names


def test_resolve_item_of_a_failed_name_does_not_reraise():
    """Same bug, worse symptom: for a previously-failed item the truthiness
    check re-raised its stored exception, so reinitialize() blew up on the
    very failure it was called to clear."""
    ns = Namespace(name="test")
    ns.append_obj(BadThing, 1, lazy=True, name="bad")
    # the state init_all's giveup_failed leaves behind: still an unresolved
    # proxy, moved into failed_items, with the failure recorded
    ns.failed_items["bad"] = ns.lazy_items.pop("bad")
    ns.failed_items_excpetion["bad"] = ValueError("boom")

    assert "bad" in ns.failed_names
    assert ns.resolve_item("bad") is not None


def test_reinitialize_clears_a_failed_name():
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")
    ns.failed_items["dependency"] = ns.lazy_items.pop("dependency")
    ns.failed_items_excpetion["dependency"] = ValueError("was broken")

    results = ns.reinitialize("dependency", verbose=False)

    assert results == {"dependency": True}
    assert "dependency" in ns.initialized_names


def test_manual_instantiation_hint_renders_an_unresolved_proxy_as_a_placeholder():
    from eco.utilities.config import format_manual_instantiation

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")
    proxy = ns.resolve_item("dependency")

    text = format_manual_instantiation(DependencyThing, [proxy], {}, name="x")

    assert "not yet initialized" in text
    assert ns.initialized_names == set(), "building the hint initialized the proxy"


class NoisyThing:
    def __init__(self, name=None):
        print("noisy device chatter from a worker thread")
        self.name = name


def _incomplete_assembly_factory():
    """An Assembly whose failure is two levels down, so the summary has to
    recurse to name the leaf that actually failed rather than the direct
    child that merely re-recorded it."""
    from eco.elements.assembly import Assembly

    def boom(name=None):
        raise RuntimeError("could not connect")

    class Leaf(Assembly):
        def __init__(self, name=None):
            super().__init__(name=name)
            self._append(boom, name="pv_x", optional=True)

    class Mid(Assembly):
        def __init__(self, name=None):
            super().__init__(name=name)
            self._append(Leaf, name="det", optional=True)

    return Mid


def test_init_all_summary_prints_even_when_silent(capsys, tmp_path):
    """print_summary used to go through the `silent`-gated log(), so at the
    (silent) defaults the summary - the one thing you always want - never
    appeared at all."""
    ns = Namespace(name="test")
    ns.append_obj(NoisyThing, lazy=True, name="good")

    ns.init_all(required_only=False, background=False, silent=str(tmp_path / "i.log"))

    out = capsys.readouterr().out
    assert "Initialized 1 of 1" in out
    assert "noisy device chatter" not in out, "worker chatter reached the terminal"
    assert "noisy device chatter" in (tmp_path / "i.log").read_text()
    assert ns.last_init_log_path == str(tmp_path / "i.log")


def test_init_all_summary_names_the_failed_subcomponent(capsys, tmp_path):
    """An incomplete item is reported with the *leaf* that failed, and kept
    apart from items that failed outright."""
    ns = Namespace(name="test")
    ns.append_obj(_incomplete_assembly_factory(), lazy=True, name="xrd")
    ns.append_obj(BadThing, 1, lazy=True, name="scans")

    ns.init_all(required_only=False, background=False, silent=str(tmp_path / "i.log"))

    out = capsys.readouterr().out
    assert "xrd (det.pv_x)" in out
    assert "scans (ValueError: boom)" in out
    assert "1 incomplete, 1 failed" in out


def test_init_all_records_a_reason_for_names_it_gives_up_on(tmp_path):
    """giveup_failed used to move a name into failed_items with no exception
    at all, leaving both the summary and the "inspect the failure" hint with
    nothing to show."""
    from eco.utilities.config import GivenUpInitialisationError, IsInitialisingError

    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="stuck")

    def always_initialising(name, verbose=True, raise_errors=False, quiet=False):
        raise IsInitialisingError(f"{name} is being initialized elsewhere")

    ns.init_name = always_initialising
    ns.init_all(
        required_only=False,
        background=False,
        silent=str(tmp_path / "i.log"),
        N_cycles=2,
    )

    assert "stuck" in ns.failed_names
    assert isinstance(
        ns.failed_items_excpetion["stuck"], GivenUpInitialisationError
    )


def test_init_all_does_not_start_a_second_overlapping_background_pass(tmp_path):
    ns = Namespace(name="test")
    ns.append_obj(DependencyThing, lazy=True, name="dependency")

    first = ns.init_all(required_only=False, silent=str(tmp_path / "i.log"))
    second = ns.init_all(required_only=False, silent=str(tmp_path / "i.log"))

    assert second is first
    assert ns.wait_for_init(timeout=30)
    assert ns.init_progress()["initialized"] == 1


def test_failed_items_exception_property_is_readable():
    """The correctly-spelled property used to return a non-existent
    attribute and raise AttributeError on any access."""
    ns = Namespace(name="test")
    exc = ValueError("boom")
    ns.failed_items_excpetion["x"] = exc

    assert ns.failed_items_exception is ns.failed_items_excpetion
    assert ns.failed_items_exception["x"] is exc


def test_init_all_captures_log_records_from_worker_threads(tmp_path):
    """A logging.StreamHandler holds the stream *object* it was built with,
    so swapping sys.stderr never affected it - which is how the bernina
    import chain's basicConfig handler put every logger.* call (eco's alias
    warnings, paramiko's SSH handshake at INFO) straight on the terminal
    during a silent init. The capture has to go through logging's own API.
    """
    import io
    import logging

    from eco.elements.assembly import Assembly

    def boom(name=None):
        raise RuntimeError("could not connect")

    class Leaf(Assembly):
        def __init__(self, name=None):
            super().__init__(name=name)
            self._append(boom, name="pv_x", optional=True)

    terminal = io.StringIO()
    handler = logging.StreamHandler(terminal)
    handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        ns = Namespace(name="test")
        ns.append_obj(Leaf, lazy=True, name="xrd")
        log = tmp_path / "i.log"

        ns.init_all(required_only=False, background=False, silent=str(log))
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    assert "failed to initialize" in log.read_text()
    assert "failed to initialize" not in terminal.getvalue()
    # the filter must be removed again, or every later record is swallowed
    assert handler.filters == []
    logging.getLogger(__name__).warning("after the pass")
    assert "after the pass" not in log.read_text()


def test_output_capture_survives_a_thread_that_outlives_the_pass():
    """Threads a device spawns can still be writing after the pass closed the
    sink (observed live: smaract stage warnings arriving after the summary).
    That used to raise ValueError inside the device's own thread."""
    import io
    import sys

    from eco.utilities.config import _ThreadRoutedOutput

    cap = _ThreadRoutedOutput(enabled=True, name="test")
    real = io.StringIO()
    orig_stderr = sys.stderr
    sys.stderr = real
    try:
        with cap:
            cap._register()
    finally:
        sys.stderr = orig_stderr

    # must not raise ValueError: I/O operation on closed file
    cap._write_sink("late output from a straggler thread")

    assert "late output" in real.getvalue()


class _DependentThing:
    def __init__(self, dep=None, name=None):
        self.name = name
        self.dep = dep


def test_declared_dependencies_are_recorded_and_layered():
    """init_all used to submit every name flat, so all thirteen consumers of
    bernina's `event_master` reached for it at once: one built it, the rest
    waited out init_timeout and came back as IsInitialisingError. Names are
    now ordered behind what they declare a NamespaceComponent dependency on.
    """
    from eco.utilities.config import NamespaceComponent

    ns = Namespace(name="test")
    ns.append_obj(_DependentThing, lazy=True, name="master")
    for n in range(3):
        ns.append_obj(
            _DependentThing,
            NamespaceComponent(ns, "master"),
            lazy=True,
            name=f"consumer{n}",
        )
    ns.append_obj(_DependentThing, lazy=True, name="unrelated")
    # a sub-attribute reference still resolves to the registered item
    ns.append_obj(
        _DependentThing,
        NamespaceComponent(ns, "master.some.child"),
        lazy=True,
        name="deep",
    )

    assert ns.get_dependencies("consumer0") == {"master"}
    assert ns.get_dependencies("deep") == {"master"}
    assert ns.get_dependencies("master") == set()

    layers = ns._dependency_layers(ns.all_names)
    assert layers[0] == {"master", "unrelated"}
    assert layers[1] == {"consumer0", "consumer1", "consumer2", "deep"}


def test_init_all_builds_a_dependency_before_its_consumers(tmp_path):
    from eco.utilities.config import NamespaceComponent

    ns = Namespace(name="test")
    ns.append_obj(_DependentThing, lazy=True, name="master")
    for n in range(4):
        ns.append_obj(
            _DependentThing,
            NamespaceComponent(ns, "master"),
            lazy=True,
            name=f"consumer{n}",
        )

    order = []
    original = ns.init_name

    def spy(name, **kwargs):
        order.append(name)
        return original(name, **kwargs)

    ns.init_name = spy
    ns.init_all(
        required_only=False,
        background=False,
        silent=str(tmp_path / "i.log"),
        max_workers=4,
    )

    assert order[0] == "master"
    assert set(order[1:]) == {f"consumer{n}" for n in range(4)}


def test_dependency_cycle_does_not_hang_and_is_emitted_as_one_layer():
    from eco.utilities.config import NamespaceComponent

    ns = Namespace(name="test")
    ns.append_obj(
        _DependentThing, NamespaceComponent(ns, "b"), lazy=True, name="a"
    )
    ns.append_obj(
        _DependentThing, NamespaceComponent(ns, "a"), lazy=True, name="b"
    )

    assert ns._dependency_layers(ns.all_names) == [{"a", "b"}]


def test_read_pv_value_retries_and_refuses_to_return_none():
    """pyepics' PV.get() returns None on a timed-out get instead of raising,
    and eco's AdjustablePv gives every channel a 50 ms connection budget with
    auto_monitor=False on the readback - so under a concurrent init_all() a
    None routinely flowed on as if it were data (an event code of None, a
    pulser number of None)."""
    import pytest

    from eco.epics_utils.adjustable import read_pv_value

    class FakePV:
        def __init__(self, values):
            self.values = list(values)
            self.pvname = "FAKE:PV"
            self.gets = 0

        def wait_for_connection(self, timeout=None):
            return True

        def get(self, timeout=None):
            self.gets += 1
            return self.values.pop(0) if self.values else None

    class FakeAdjustable:
        def __init__(self, pv):
            self._pvreadback = pv
            self.name = "fake"

    # a transient timeout is retried, not taken at face value
    adj = FakeAdjustable(FakePV([None, None, 27]))
    assert read_pv_value(adj, timeout=0.01, attempts=3) == 27
    assert adj._pvreadback.gets == 3

    # a persistent one raises with the PV named, instead of returning None
    adj = FakeAdjustable(FakePV([]))
    with pytest.raises(TimeoutError) as excinfo:
        read_pv_value(adj, timeout=0.01, attempts=2)
    assert "FAKE:PV" in str(excinfo.value)

    # a legitimate falsy value is a value, not a failure
    adj = FakeAdjustable(FakePV([0]))
    assert read_pv_value(adj, timeout=0.01, attempts=2) == 0
