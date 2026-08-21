"""Structural interface StepScan expects from a "counter" (one entry in
``Scans``'/``StepScan``'s ``counters`` list, e.g. ``Daq``, ``CounterValue``,
``EpicsDaq``).

Counters have always been duck-typed: ``StepScan.do_next_step`` calls
``acquire``/``start``/``stop`` directly, and ``run_callbacks_*`` probe
``hasattr(ctr, "callbacks_*")`` before using each callback list. This module
documents that existing contract in one place (for static checking, editor
tooltips, and onboarding); it does not change the duck-typed dispatch in
``scan.py`` -- a counter implementing this shape works exactly as before,
nothing here is enforced at runtime.
"""
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class AcquisitionHandle(Protocol):
    """Returned by ``Counter.acquire()``. Matches
    ``eco.acquisition.utilities.Acquisition``: ``.wait()`` blocks until the
    acquisition is done; ``.file_names`` (optional) lists produced files."""

    def wait(self) -> Any: ...


@runtime_checkable
class Counter(Protocol):
    """One entry in a scan's ``counters`` list.

    StepScan picks one of two acquisition modes per step, chosen by whether
    *any* counter (or the scan itself) has a non-empty
    ``callbacks_step_counting``:

    - simple mode: ``acquire(scan, Npulses=..., **kwargs)`` returns an
      ``AcquisitionHandle``; StepScan calls ``.wait()`` on it and reads
      ``.file_names`` if present.
    - start/stop mode: ``start(scan, **kwargs)`` begins the acquisition,
      then (after ``scan.run_callbacks_step_counting()`` runs across all
      counters/scan callbacks) ``stop(scan, **kwargs)`` returns a dict with
      a ``"files"`` key.

    All five ``callbacks_*`` list attributes are optional -- a counter only
    needs to define the ones it actually uses (e.g. ``CounterValue.start``/
    ``.stop`` are no-op stubs kept only so the attributes exist; it does its
    real work through ``callbacks_start_scan``/``callbacks_end_step``/
    ``callbacks_end_scan`` instead).
    """

    name: str

    def acquire(
        self, scan: Optional[Any] = None, Npulses: Optional[int] = None, **kwargs
    ) -> Any: ...

    def start(self, scan: Optional[Any] = None, **kwargs) -> Any: ...

    def stop(self, scan: Optional[Any] = None, **kwargs) -> Dict[str, Any]: ...

    callbacks_start_scan: List[Any]
    callbacks_start_step: List[Any]
    callbacks_step_counting: List[Any]
    callbacks_end_step: List[Any]
    callbacks_end_scan: List[Any]
