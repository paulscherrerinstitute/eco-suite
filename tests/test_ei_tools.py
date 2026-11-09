"""%ei's lookups (eco.ei_tools), the logbook code check (eco.ei_logcheck),
the knowledge packs and the storage sheet (eco.utilities.storage_locations)."""
import json
import re

from _light_eco import ensure_eco_importable

ensure_eco_importable()

import eco.ei as ei
import eco.ei_check as ec
import eco.ei_logcheck as lc
import eco.ei_tools as et
import eco.logs_ask as la
from eco.utilities import storage_locations as sl

from test_ei_check import NS, _symbols
from test_ei import _config


# -- the tool loop ----------------------------------------------------------------------------


class _ToolBackend:
    """Calls `describe` first, then answers; records what it was sent."""

    def __init__(self, calls_per_round=1, rounds=1, refuse_tools=False):
        self.sent, self.rounds, self.per_round, self.refuse = [], rounds, calls_per_round, refuse_tools

    def chat_round(self, messages, tools=None, on_delta=None, stop=None):
        self.sent.append((messages, tools))
        if self.refuse and tools:
            raise RuntimeError("400 tools not supported")
        if tools and self.rounds:
            self.rounds -= 1
            return "", [{"id": f"c{i}", "name": "describe", "arguments": json.dumps({"name": "stage.x"})}
                        for i in range(self.per_round)]
        return "Use `stage.x.mv(1)`.", []

    def chat(self, messages, on_delta=None, stop=None):
        self.sent.append((messages, None))
        return "plain answer"


def _assistant(tmp_path, backend):
    tmp_path = tmp_path / "asst"  # its own symbol table, next to any other in the test
    tmp_path.mkdir()
    cfg = _config()
    cfg["storage"] = False
    a = ei.Assistant(None, config=cfg, backend=backend, db_path=tmp_path / "i.sqlite")
    la._connect(a.db_path).close()
    a._indexed_at = 1e18
    a._symbols = _symbols(tmp_path)
    a.namespace.entries = lambda: NS
    return a


def test_the_model_looks_up_then_answers(tmp_path):
    backend = _ToolBackend()
    a = _assistant(tmp_path, backend)
    phases = []
    ans = a.ask("how do I move stage x?", tools=True, report=None, on_phase=phases.append)
    assert ans.text == "Use `stage.x.mv(1)`."
    [(name, args, result)] = ans.tool_calls
    assert name == "describe" and "Motor" in result and "mv" in result
    # the result went back to the model as a tool message for that call
    last = backend.sent[-1][0]
    assert last[-1]["role"] == "tool" and last[-1]["tool_call_id"] == "c0"
    assert last[-2]["tool_calls"][0]["function"]["name"] == "describe"
    assert any(p.startswith("looking up describe stage.x") for p in phases)


def test_the_lookups_are_limited_and_a_refusing_endpoint_falls_back(tmp_path):
    backend = _ToolBackend(calls_per_round=4, rounds=3)
    a = _assistant(tmp_path, backend)
    a.config["tool_calls"] = 5
    ans = a.ask("q", tools=True, report=None)
    assert len(ans.tool_calls) == 5
    assert backend.sent[-1][1] is None  # the last round without tools: it has to answer
    refused = [m for m in backend.sent[-1][0] if m["role"] == "tool" and "no more lookups" in m["content"]]
    assert refused  # calls beyond the budget are answered, not run

    backend = _ToolBackend(refuse_tools=True)
    reports = []
    (tmp_path / "second").mkdir()
    ans = _assistant(tmp_path / "second", backend).ask("q", tools=True, report=reports.append)
    assert ans.text == "plain answer" and not ans.tool_calls
    assert "answering without them" in reports[0]


def test_tools_report_errors_to_the_model(tmp_path):
    box = et.Toolbox(_assistant(tmp_path, _ToolBackend()))
    assert box.run("describe", "{not json").startswith("error: arguments are not valid JSON")
    assert box.run("rm_rf", "{}").startswith("error: no tool 'rm_rf'")
    assert box.run("describe", json.dumps({"nme": "x"})).startswith("error:")
    assert "line 1: `stage.z`" in box.run("check_code", json.dumps({"code": "stage.z.mv(1)"}))
    assert "no problems" in box.run("check_code", json.dumps({"code": "stage.x.mv(1)"}))


def test_describe_reads_the_source_and_never_builds(tmp_path):
    sym = _symbols(tmp_path)
    text = et.describe("stage", sym, NS)
    assert "components: x (Motor), y (Motor)" in text
    assert "has no z" in et.describe("stage.z", sym, NS)
    assert et.describe("stage.x.set_target_value", sym, NS).startswith("stage.x.set_target_value(value, hold)")
    assert "did you mean stage" in et.describe("stag", sym, NS)

    class Lazy:  # an unbuilt namespace Proxy: must stay unbuilt
        __resolved__ = False

        def __getattr__(self, name):
            raise AssertionError("built")

    class Live:
        def __init__(self):
            self.alias = object()

        @property
        def value(self):
            raise AssertionError("read")

        def go(self, where, fast=False):
            """Go somewhere."""

    assert "is built on first use" not in et.describe("stage", sym, NS, {"stage": Lazy()})
    live = Live()
    assert "go(where, fast)" in et.describe("dev", sym, NS, {"dev": live})
    assert "not read" in et.describe("dev.value", sym, NS, {"dev": live})


# -- the logbook check ------------------------------------------------------------------------


def test_code_lines_finds_code_not_prose():
    text = """Run 552: alignment

    scans.ascan(stage.x, 0, 1, 10, 100,
                description="x")
    then stage.x looked fine (scan 3).
    >>> stage.y.mv(2)
    plt.plot(x)
    """
    lines = [code for _n, code in lc.code_lines(text, {"stage", "scans"})]
    assert lines == ['scans.ascan(stage.x, 0, 1, 10, 100,\n                description="x")', "stage.x", "stage.y.mv(2)"]
    # names in prose are checked as attribute chains; addresses and files are not code
    prose = "see https://elog.psi.ch/x, run0552_.json, mail elog.admin@psi.ch; use elog.post or stage.z"
    assert [c for _n, c in lc.code_lines(prose, {"elog", "stage", "run0552_"})] == ["elog.post", "stage.z"]


def test_outdated_logbook_code_is_found_and_flagged(tmp_path):
    sym = _symbols(tmp_path)
    db = tmp_path / "i.sqlite"
    con = la._connect(db)
    rows = [
        ("moved it: stage.z.mv(3)", "a [lb]", "scilog", "2000", "scilog:L:P1"),
        ("again stage.z.mv(4) and stage.x.mvv(1)", "a [lb]", "scilog", "3000", "scilog:L:P2"),
        ("stage.x.mv(1) all fine", "a [lb]", "scilog", "4000", "scilog:L:P3"),
        ("stage.z.mv(5)", "doc", "doc", "0", "docs/x.md"),  # docs are not logbooks
    ]
    for text, context, kind, t, path in rows:
        con.execute("INSERT INTO items (text, context, kind, status, flags, t, source, path) VALUES (?,?,?,?,?,?,?,?)",
                    (text, context, kind, "ok", "", t, "s", path))
    con.commit()
    con.close()
    checker = lc.Checker(sym, NS)
    assert checker.problems_of("table: stage.x.pvn | 3") == []  # cut off: stage.x.pvname
    assert checker.problems_of("stage.x.pvn(3)") != []  # code: wrong, however close
    findings = lc.check_index(sym, db_path=db, namespace=NS)
    assert [(f.code, len(f.items)) for f in findings] == [("stage.z", 2), ("stage.x.mvv", 1)]
    assert "(it has:" not in findings[0].message  # the long member list stays out of the report
    assert lc.check_index(sym, db_path=db, namespace=NS, since=2500)[0].items[0][0] == 3000
    assert "stage.z" in lc.report(findings)

    # %ei tells the model not to copy it
    a = _assistant(tmp_path, _ToolBackend())
    a.config["check_code"] = True
    hit = la.Hit("scilog", rows[0][0], "a [lb]", "ok", "", 2000, "s", 0, "scilog:L:P1")
    assert "!! code check" in a.context_item(hit) and "stage.z" in a.context_item(hit)
    fine = la.Hit("scilog", rows[2][0], "a [lb]", "ok", "", 4000, "s", 0, "scilog:L:P3")
    assert "!!" not in a.context_item(fine)


def test_static_namespace_reads_the_instrument_definition(tmp_path):
    (tmp_path / "inst.py").write_text(
        'namespace.append_obj("Mono", "PV", name="mono", module_name="eco.x.mono", lazy=True)\n'
        "namespace.append_obj(make_thing, name=\"thing\")\n"
        'namespace.append_obj("Mono", name=some_variable)\n'
    )
    assert ec.static_namespace([tmp_path]) == [("mono", "Mono", "eco.x.mono", "lazy"), ("thing", "make_thing", "", "lazy")]
    names = {n for n, *_ in ec.static_namespace()}
    assert {"scans", "mono", "daq", "elog"} <= names


# -- knowledge packs ----------------------------------------------------------------------------


def test_packs_are_chosen_by_their_words(tmp_path, monkeypatch):
    (tmp_path / "scans.md").write_text("---\ntitle: Scans\nwords: [scan, \"%queue\"]\n---\nuse scans.ascan\n")
    (tmp_path / "motors.md").write_text("---\ntitle: Motors\nwords: [motor, tweak]\n---\nmv\n")
    monkeypatch.setattr(ei, "PACKS_DIR", tmp_path)
    monkeypatch.setattr(ei, "USER_PACKS_DIR", tmp_path / "none")
    a = _assistant(tmp_path, _ToolBackend())
    assert sorted(t for t, _ in a.packs("how do I scan a motor?")) == ["Motors", "Scans"]
    assert [t for t, _ in a.packs("scan a motor, then tweak it")] == ["Motors", "Scans"]  # more words first
    assert [t for t, _ in a.packs("what does %queue do")] == ["Scans"]
    assert a.packs("the weather") == []
    a.config["packs"] = 0
    assert a.packs("scan") == []
    a.config["packs"] = 2
    system = a.messages("tweak a motor", [])[0]["content"]
    assert "Motors -- eco's maintainers' guide" in system and "Scans --" not in system


def test_the_shipped_packs_fit_eco_today():
    """A pack goes stale when eco changes; its code is checked like an answer's."""
    packs = ei.load_packs([ei.PACKS_DIR])
    assert {p["name"] for p in packs} >= {"scans", "motors", "daq", "logbooks"}
    sym = ec.build([la.SRC_DIR])
    ns = ec.static_namespace()
    for p in packs:
        assert p["words"] and p["title"]
        blocks = re.findall(r"```python\n(.*?)```", p["text"], re.S)
        inline = [c for c in re.findall(r"`([^`\n]+)`", p["text"]) if re.match(r"^[a-z_]+\.[\w.]+\(", c)]
        for code in blocks + inline:
            problems = ec.check(code, sym, ns)
            assert problems == [], f"{p['name']}: {code!r}: {[str(x) for x in problems]}"


# -- storage sheet --------------------------------------------------------------------------------


MAP_CSV = """500,,,,,,Name,Room number,Description,Details / Special,Available space,Locking system,Code
,,,,,,BC6,EH.002f,"PSI standard cabinet, solid doors",,Full,Key,
,,,,,,BD1,EH.055,"Lista roller",Mechanics,1/2 drawer empty,Code lock,1276
,,,,,,SC1 (Alvrina 1),EH.002f,"PSI standard cabinet",Shared,Full,Key,
,,,,,,,,,,,,
Photos,,,,,,,,,BD4,,,
"""
ITEMS_CSV = """Item,space needed,location ,sub-location,comment
Accessoires PI Hexapod,1/2 shelf,BC6,,
Screws and nuts,,BD 1,,
Chiller accessories,,SC1,lower,
Big plastic foil,,BC6 / BD1,,
Fiberglass rods,,BC6 / BC-C1,,long ones
Hand tools,,BC6, bottom,
"""


def test_storage_sheet_is_joined_and_keeps_no_codes():
    locations = sl.parse_map(MAP_CSV)
    assert set(locations) == {"BC6", "BD1", "SC1"}
    assert "1276" not in json.dumps({k: vars(v) for k, v in locations.items()})
    assert locations["SC1"].label == "SC1 (Alvrina 1)" and locations["BD1"].lock == "Code lock"
    st = sl.Storage(sl.parse_items(ITEMS_CSV, locations), locations, fetched=1)
    [hexapod] = st.where("hexapd")  # a typo
    assert hexapod.where() == "BC6 (EH.002f, PSI standard cabinet, solid doors)"
    assert [i.item for i in st.where("screws")] == ["Screws and nuts"]  # "BD 1" is BD1
    assert {i.item for i in st.contents("bd1")} == {"Screws and nuts", "Big plastic foil"}
    assert st.lookup("SC1 (Alvrina 1)")[0].name == "SC1"
    assert [p.name for p in st.where("foil")[0].places] == ["BC6", "BD1"]
    assert st.where("hand tools")[0].sub_location == "bottom"
    assert any("'BC-C1' is not in the storage map (did you mean BC6?" in p or "'BC-C1'" in p for p in st.check())


def test_storage_context_only_for_storage_questions(monkeypatch):
    locations = sl.parse_map(MAP_CSV)
    st = sl.Storage(sl.parse_items(ITEMS_CSV, locations), locations, fetched=1)
    monkeypatch.setattr(sl, "load", lambda *a, **k: st)
    assert "Accessoires PI Hexapod: BC6" in sl.context_for("where is the hexapod stored?")
    assert sl.context_for("how do I scan the hexapod?") == ""
    assert "contents of BC6" in sl.context_for("what is stored in BC6?")


def test_eco_needs_no_import_line(tmp_path):
    sym = _symbols(tmp_path)
    assert ec.check("eco.motors.Stage()", sym, NS) == []
    assert [p.code for p in ec.check("eco.motors.Stag()", sym, NS)] == ["eco.motors.Stag"]
    assert ec.check("eco = 3\neco.real", sym, NS) == []  # a local eco is not the package
