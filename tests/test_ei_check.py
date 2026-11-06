"""eco.ei_check: an answer's code read against the source and the session
(never run), and %ei asking the model once to correct what does not fit."""
import json
import textwrap

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.ei as ei
import eco.ei_check as ec

PKG = {
    "__init__.py": "",
    "elements.py": '''
        def spec_convenience(Adj, value_name="_value"):
            Adj.mv = lambda self, v: None
            Adj.wm = lambda self: None
            setattr(Adj, value_name, property(lambda self: 1))
            return Adj

        class Assembly:
            def __init__(self, name=None):
                self.name = name

            def _append(self, factory, *args, name=None, **kwargs):
                self.__dict__[name] = factory(*args, **kwargs)

            def status(self):
                pass

            def add(self, obj, name):
                self._append(obj, name=name)  # a helper: its caller names it
        ''',
    "motors.py": '''
        from .elements import Assembly, spec_convenience

        @spec_convenience
        class Motor(Assembly):
            def __init__(self, pv, name=None):
                super().__init__(name=name)
                self.pvname = pv

            def set_target_value(self, value, hold=False):
                pass

            @property
            def limits(self):
                return (0, 1)

        class Stage(Assembly):
            def __init__(self, name=None):
                super().__init__(name=name)
                self._append(Motor, "X", name="x")
                self._append(Motor, "Y", name="y")

        class Channels(Assembly):
            def __init__(self, n, name=None):
                super().__init__(name=name)
                for i in range(n):
                    self._append(Motor, f"C{i}", name=f"ch{i}")  # built in a loop: not judged
        ''',
}


def _symbols(tmp_path):
    pkg = tmp_path / "eco"
    pkg.mkdir()
    for name, text in PKG.items():
        (pkg / name).write_text(textwrap.dedent(text))
    return ec.build([pkg], cache_path=tmp_path / "sym.json")


NS = [("stage", "Stage", "eco.motors", "lazy"), ("chans", "Channels", "eco.motors", "lazy")]


def _messages(code, sym, user_ns=None):
    return [str(p) for p in ec.check(textwrap.dedent(code), sym, NS, user_ns)]


def test_attributes_components_and_keywords_are_read_from_the_source(tmp_path):
    sym = _symbols(tmp_path)
    ok = """
        stage.x.mv(1)
        stage.y.wm()
        stage.x.set_target_value(2, hold=True)
        stage.x.pvname
        stage.x.limits
        stage.status()
        chans.ch3.mv(1)
        chans.anything_at_all
        m = stage.x
        m.whatever()
        %ei a question
        stage.x?
        import numpy as np
        np.sum([1])
        """
    assert _messages(ok, sym) == []
    bad = """
        stage.z.mv(1)
        stage.x.move(1)
        stage.x.set_target_value(2, wait=True)
        """
    assert _messages(bad, sym) == [
        "line 2: `stage.z` -- Stage has no z (it has: add, name, status, x, y)",
        "line 3: `stage.x.move` -- Motor has no move (did you mean mv?)",
        "line 4: `stage.x.set_target_value(..., wait=...)` -- takes no keyword wait; its parameters: value, hold",
    ]


def test_imports_and_syntax(tmp_path):
    sym = _symbols(tmp_path)
    assert _messages("from eco.motors import Stage, Motr\nStage(nme='s')\n", sym) == [
        "line 1: `from eco.motors import Motr` -- eco.motors has no Motr (did you mean Motor?)",
        "line 2: `Stage(..., nme=...)` -- takes no keyword nme (did you mean name?); its parameters: name",
    ]
    assert _messages("import eco.motrs\n", sym) == ["line 1: `import eco.motrs` -- there is no module eco.motrs (did you mean eco.motors?)"]
    assert _messages("stage.x.mv(1\n", sym)[0].startswith("line 1: `stage.x.mv(1` -- is not valid Python")
    assert ec.check("%%queue\nanything goes", sym) == []


def test_live_objects_are_read_without_building_or_evaluating_anything(tmp_path):
    sym = _symbols(tmp_path)

    class Probe:
        evaluated = False

        def __init__(self):
            self.child = type("Child", (), {"go": lambda self, speed=1: None})()

        @property
        def expensive(self):
            Probe.evaluated = True
            return 1

    class LazyProxy:  # an unresolved namespace Proxy
        __resolved__ = False

        def __getattribute__(self, name):
            if name == "__resolved__":
                return False
            raise AssertionError("touched an unresolved proxy")

    user_ns = {"probe": Probe(), "stage": LazyProxy()}
    assert _messages("probe.child.go(speed=2)\nprobe.expensive.real\n", sym, user_ns) == []
    assert not Probe.evaluated
    assert _messages("probe.chlid.go()\nprobe.child.go(sped=2)\n", sym, user_ns) == [
        "line 1: `probe.chlid` -- Probe has no chlid (did you mean child?)",
        "line 2: `probe.child.go(..., sped=...)` -- takes no keyword sped (did you mean speed?); its parameters: speed",
    ]
    # the unresolved proxy is judged from the source instead
    assert _messages("stage.z.mv(1)\n", sym, user_ns) == ["line 1: `stage.z` -- Stage has no z (it has: add, name, status, x, y)"]
    assert _messages("stgae.x.mv(1)\n", sym, user_ns) == ["line 1: `stgae` -- stgae is not defined in this session (did you mean stage?)"]


def test_the_cache_follows_the_files(tmp_path):
    sym = _symbols(tmp_path)
    assert sym.attribute("Stage", "x")[0] is True
    cache = json.loads((tmp_path / "sym.json").read_text())
    assert cache["version"] == ec._VERSION and len(cache["files"]) == 3
    motors = tmp_path / "eco" / "motors.py"
    motors.write_text(motors.read_text().replace('name="y"', 'name="yy"'))
    sym = ec.build([tmp_path / "eco"], cache_path=tmp_path / "sym.json")
    assert sym.attribute("Stage", "yy")[0] is True and sym.attribute("Stage", "y")[0] is False


class _Backend:
    """Answers with bad code first, then with what `fixed` says."""

    def __init__(self, fixed):
        self.fixed, self.calls = fixed, []

    def chat(self, msgs, on_delta=None, stop=None):
        self.calls.append(msgs)
        text = "Move it:\n```python\nstage.z.mv(1)\n```" if len(self.calls) == 1 else self.fixed
        if on_delta:
            on_delta(text)
        return text


def _assistant(tmp_path, backend):
    cfg = ei.load_config(tmp_path / "ei.json")
    cfg.update(scilog_default=None, elog_logbooks=None)
    a = ei.Assistant(None, config=cfg, backend=backend, db_path=tmp_path / "i.sqlite")
    a._indexed_at = 1e18
    a.retrieve = lambda q, follow_up=True: []
    a._symbols = _symbols(tmp_path)
    a.namespace.entries = lambda: NS
    return a


def test_the_model_corrects_its_code_once_and_what_remains_is_noted(tmp_path):
    good = _Backend("Move it:\n```python\nstage.x.mv(1)\n```")
    a = _assistant(tmp_path, good)
    shown, revised = [], []
    ans = a.ask("move the stage", report=None, on_delta=shown.append, on_revise=revised.append)
    assert len(good.calls) == 2 and "Stage has no z (it has: add, name, status, x, y)" in good.calls[1][-1]["content"]
    assert ans.text.endswith("stage.x.mv(1)\n```") and ans.problems == [] and ans.revised
    assert len(revised) == 1 and a.history[-1][1] == ans.text

    (tmp_path / "b").mkdir()
    still_bad = _Backend("Sorry:\n```python\nstage.x.move(1)\n```")
    b = _assistant(tmp_path / "b", still_bad)
    shown = []
    ans = b.ask("move the stage", report=None, on_delta=shown.append)
    assert len(still_bad.calls) == 2  # corrected once, not again
    assert "**Not verified:**" in ans.text and "Motor has no move" in ans.text
    assert "Corrected after checking" in "".join(shown) and shown[-1].startswith("\n\n> **Not verified:**")

    (tmp_path / "c").mkdir()
    off = _assistant(tmp_path / "c", _Backend(""))
    off.config["check_code"] = False
    assert off.ask("move the stage", report=None).problems is None
