"""When an account can't create a new directory in the shared memory tree
(eco_cnf_bernina/memory/ -- e.g. the gac-bernina console account, whose
default group isn't the one that tree is setgid to, and that isn't
something eco can fix by chmod'ing anything), Memory.setup_path() falls
back to a private, per-account directory under the home dir instead of
losing memories/presets outright. See test_assembly_memory_degrades.py for
the outer safety net (Assembly construction itself must never crash even
if this fallback also fails)."""
from pathlib import Path

from eco.elements import memory
from eco.elements.assembly import Assembly


def test_setup_path_falls_back_to_home_dir_when_the_shared_one_is_unwritable(
    monkeypatch, tmp_path
):
    shared_dir = tmp_path / "shared"
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    monkeypatch.setattr(memory, "global_memory_dir", shared_dir)
    # simulate a shared directory this account can never create in, without
    # actually needing a second unprivileged account in the test
    monkeypatch.setattr(memory, "ensure_dir", lambda *a, **k: None)
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    asm = Assembly(name="some_device")

    assert asm.memory is not None
    assert not (shared_dir / "some_device").exists()
    assert asm.memory._memories() == {}
    assert asm.memory.dir == home_dir / ".eco" / "memory" / "some_device"


def test_setup_path_uses_the_shared_dir_when_it_is_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "global_memory_dir", tmp_path)

    asm = Assembly(name="some_device")

    assert asm.memory.dir == tmp_path / "some_device"
    assert asm.memory.dir.is_dir()
