"""Audit-sink behaviour of eco.elements.access.

The point of interest is that the trail must survive an unwritable path: a
write gate runs on every set_target_value, so a broken sink that warned each
time would make every motor move noisy (and the old fixed /tmp path *was*
unwritable for the second account sharing a console).
"""

import getpass
import logging
from pathlib import Path

import pytest

from eco.elements import access


@pytest.fixture
def audit_state(monkeypatch):
    """Isolate the module-level audit sink state for one test."""
    monkeypatch.setattr(access, "_audit_path", None, raising=False)
    monkeypatch.setattr(access, "_audit_source", None, raising=False)
    monkeypatch.setattr(access, "_audit_disabled", False, raising=False)
    monkeypatch.setattr(access, "audit", True)
    return access


def _log(n=1):
    identity = access.Identity("tester", ["p12345"])
    for _ in range(n):
        access._log_audit(identity, "some.motor", True, "no rule", False)


def test_default_audit_path_is_per_user(monkeypatch):
    monkeypatch.delenv("ECO_ACCESS_AUDIT", raising=False)
    from eco.utilities.tempfiles import user_temp_path

    path = Path(user_temp_path("eco_access_audit.log"))
    assert getpass.getuser() in path.name
    assert path.name != "eco_access_audit.log"


def test_writes_a_line_to_the_configured_path(audit_state, tmp_path, monkeypatch):
    target = tmp_path / "audit.log"
    monkeypatch.setattr(access, "AUDIT_PATH", target)
    _log()
    assert "some.motor" in target.read_text()
    assert "ALLOW" in target.read_text()


def test_reconfigured_audit_path_is_picked_up(audit_state, tmp_path, monkeypatch):
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    monkeypatch.setattr(access, "AUDIT_PATH", first)
    _log()
    monkeypatch.setattr(access, "AUDIT_PATH", second)
    _log()
    assert first.exists() and second.exists()


def test_unwritable_path_falls_back_per_user_and_warns_once(
    audit_state, tmp_path, monkeypatch, caplog
):
    unwritable = tmp_path / "nonexistent-dir" / "audit.log"
    fallback = tmp_path / f"eco_access_audit_{getpass.getuser()}.log"
    monkeypatch.setattr(access, "AUDIT_PATH", unwritable)
    monkeypatch.setattr(access, "user_temp_path", lambda name: str(fallback))
    with caplog.at_level(logging.WARNING, logger=access.logger.name):
        _log(5)
    assert fallback.read_text().count("some.motor") == 5
    assert len([r for r in caplog.records if "audit" in r.message]) == 1


def test_gives_up_quietly_when_the_fallback_fails_too(
    audit_state, tmp_path, monkeypatch, caplog
):
    unwritable = tmp_path / "nonexistent-dir" / "audit.log"
    monkeypatch.setattr(access, "AUDIT_PATH", unwritable)
    monkeypatch.setattr(
        access, "user_temp_path", lambda name: str(tmp_path / "also-missing" / name)
    )
    with caplog.at_level(logging.WARNING, logger=access.logger.name):
        _log(5)
    assert access._audit_disabled is True
    assert len([r for r in caplog.records if "audit" in r.message]) == 2
