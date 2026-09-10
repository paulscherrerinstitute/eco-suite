"""A brand new namespace object's memory/presets directory doesn't exist yet,
and not every account that can construct namespace objects is in the group
that can create one in the shared memory tree (eco_cnf_bernina/memory/) --
this hit StatusServer live: an account outside unx-sf_bernina_bs got
`PermissionError` from `ensure_dir`, which `AdjustableFS._write_value` then
turned into an uncaught `FileNotFoundError`, taking the entire Assembly
construction down. Memory/presets is a nice-to-have on top of an object, not
load-bearing for whether it can be constructed at all, so Assembly.__init__
now degrades to `self.memory = None` (a state other code already expects,
see memory.py's own `getattr(ancestor, "memory", None)`) instead of letting
that failure propagate."""
from eco.elements import memory
from eco.elements.assembly import Assembly


def test_assembly_construction_survives_a_memory_setup_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "global_memory_dir", tmp_path)

    def _boom(self, *args, **kwargs):
        raise PermissionError("could not create memory directory")

    monkeypatch.setattr(memory.Memory, "__init__", _boom)

    asm = Assembly(name="brand_new_thing")

    assert asm.memory is None


def test_assembly_memory_still_works_when_the_directory_is_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "global_memory_dir", tmp_path)

    asm = Assembly(name="normal_thing")

    assert asm.memory is not None
    assert isinstance(asm.memory, memory.Memory)
