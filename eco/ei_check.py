"""eco.ei_check -- does the code in a %ei answer fit eco as it is today?

The model answers from logbook posts and commands people ran, some of them
from older versions of eco, so a suggestion can use a method, a keyword or a
component that does not exist (any more). check() reads the code -- it is
never run -- and reports, per line:

* a syntax error;
* an attribute that the object does not have: `xrd.foo`, `scans.ascna`;
* a keyword the function does not take: `scans.ascan(..., Npulses=100)`;
* an `import` of an eco module or name that does not exist;
* a device name that is not in the session (only in a session).

Objects are looked up live where the session has them already built (with
inspect.getattr_static: no property is evaluated, no lazy device is built),
otherwise statically: a table of every class of eco's source -- methods
with their parameters, attributes set on `self`, the components it
_append()s with their classes, what class decorators add -- built with
`ast` and cached per file next to the search index. Where an object is
dynamic (`__getattr__`, `setattr` with a computed name, a base class
outside the table) nothing is claimed.
"""

import ast
import builtins
import difflib
import inspect
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from eco import logs_ask

SYMBOLS_NAME = "ei_symbols.json"
_VERSION = 4
#: base classes that add nothing a user would call on a device
_PLAIN_BASES = {"object", "Protocol", "Generic", "ABC"}
#: names never judged: common modules people import themselves
_EXTERNAL = {
    "np", "numpy", "plt", "matplotlib", "pd", "pandas", "time", "os", "sys", "math", "datetime",
    "json", "re", "pathlib", "Path", "escape", "scipy", "epics", "subprocess", "itertools", "random",
}


@dataclass
class Problem:
    line: int
    code: str
    message: str

    def __str__(self):
        return f"line {self.line}: `{self.code}` -- {self.message}"


# -- the table of eco's source -----------------------------------------------------------


def _sig(fn):
    a = fn.args
    params = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    return {"params": params, "varkw": a.kwarg is not None}


def _name_of(node):
    """Last part of a (dotted) name or call: MotorRecord, eco.x.MotorRecord, Dec(...)."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _class_entry(node, module):
    entry = {"module": module, "bases": [], "methods": {}, "attrs": [], "components": {}, "open": False,
             "decorators": [d for d in map(_name_of, node.decorator_list) if d]}
    for b in node.bases:
        name = _name_of(b)
        if name is None:
            entry["open"] = True
        else:
            entry["bases"].append(name)
    if any(k.arg == "metaclass" for k in node.keywords):
        entry["open"] = True
    attrs = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name in ("__getattr__", "__getattribute__"):
                entry["open"] = True
            decorators = {_name_of(d) for d in item.decorator_list}
            if decorators & {"property", "cached_property"} or any(
                isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter") for d in item.decorator_list
            ):
                attrs.add(item.name)
            else:
                sig = _sig(item)
                if "staticmethod" not in decorators and sig["params"]:
                    sig["params"] = sig["params"][1:]  # self / cls
                entry["methods"][item.name] = sig
        elif isinstance(item, ast.ClassDef):
            attrs.add(item.name)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            for t in item.targets if isinstance(item, ast.Assign) else [item.target]:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        attrs.add(n.id)
    # an _append whose name is a parameter of its method passes on what the
    # caller names (Assembly.add(obj, name)); any other computed name makes
    # the class's components unknowable
    passthrough = {}
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = {a.arg for a in item.args.posonlyargs + item.args.args + item.args.kwonlyargs}
            # a parameter the method itself overwrites is computed after all
            params -= {m.id for m in ast.walk(item) if isinstance(m, ast.Name) and isinstance(m.ctx, ast.Store)}
            for n in ast.walk(item):
                passthrough[id(n)] = params
    for n in ast.walk(node):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for t in n.targets if isinstance(n, ast.Assign) else [n.target]:
                for x in ast.walk(t):
                    if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "self":
                        attrs.add(x.attr)
        elif isinstance(n, ast.Call):
            fname = _name_of(n.func)
            on_self = isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"
            if fname == "setattr" and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "self":
                if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                    attrs.add(n.args[1].value)
                else:
                    entry["open"] = True
            elif fname == "_append" and on_self:
                name = next((kw.value for kw in n.keywords if kw.arg == "name"), None)
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    entry["components"][name.value] = _name_of(n.args[0]) if n.args else None
                elif not (isinstance(name, ast.Name) and name.id in passthrough.get(id(n), ())):
                    entry["open"] = True  # computed here: a loop, a parameter list ...
    for method in node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method.name == "_append":
            continue  # _append fills __dict__ with the components, seen above
        for n in ast.walk(method):
            # writing self.__dict__[computed] or self.__dict__.update(...)
            is_dict = lambda x: (isinstance(x, ast.Attribute) and x.attr == "__dict__"  # noqa: E731
                                 and isinstance(x.value, ast.Name) and x.value.id == "self")
            if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) and is_dict(n.value):
                if not (isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)):
                    entry["open"] = True
                else:
                    attrs.add(n.slice.value)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update" and is_dict(n.func.value):
                entry["open"] = True
    entry["attrs"] = sorted(attrs - set(entry["methods"]))
    return entry


def _decorator_entry(fn):
    """What a function used as a class decorator sets on its first argument."""
    if not fn.args.args:
        return None
    target = fn.args.args[0].arg
    # a parameter with a string default names what it sets: value_name="_value"
    positional = fn.args.posonlyargs + fn.args.args
    defaults = {
        a.arg: d.value
        for a, d in zip(positional[len(positional) - len(fn.args.defaults):], fn.args.defaults)
        if isinstance(d, ast.Constant) and isinstance(d.value, str)
    }
    defaults.update({
        a.arg: d.value for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults)
        if isinstance(d, ast.Constant) and isinstance(d.value, str)
    })
    names, open_ = set(), False
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in n.targets if isinstance(n, ast.Assign) else [n.target]:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == target:
                    names.add(t.attr)
        elif isinstance(n, ast.Call) and _name_of(n.func) == "setattr" and n.args:
            if isinstance(n.args[0], ast.Name) and n.args[0].id == target:
                if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                    names.add(n.args[1].value)
                elif len(n.args) > 1 and isinstance(n.args[1], ast.Name) and n.args[1].id in defaults:
                    names.add(defaults[n.args[1].id])
                else:
                    open_ = True
    if not names and not open_:
        return None
    return {"names": sorted(names), "open": open_}


def _file_symbols(path, module):
    import warnings

    try:
        with warnings.catch_warnings():  # old files' invalid escape sequences
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(Path(path).read_text(errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return None
    out = {"module": module, "classes": {}, "functions": {}, "decorators": {}, "names": []}
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out["classes"][node.name] = _class_entry(node, module)
            names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["functions"][node.name] = _sig(node)
            dec = _decorator_entry(node)
            if dec:
                out["decorators"][node.name] = dec
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.If, ast.Try)):
            for n in ast.walk(node):  # conditional definitions and imports
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                    names.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    names.update((a.asname or a.name).split(".")[0] for a in n.names)
                elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    names.add(n.id)
    out["names"] = sorted(names)
    return out


def _module_name(path, root):
    rel = Path(path).relative_to(root).with_suffix("")
    return ".".join((Path(root).name, *rel.parts)).removesuffix(".__init__")


def build(src_dirs, cache_path=None):
    """The Symbols of every .py file under `src_dirs` (package directories),
    re-parsing only files changed since the cache at `cache_path`."""
    cache_path = Path(cache_path or logs_ask.index_dir() / SYMBOLS_NAME)
    try:
        cached = json.loads(cache_path.read_text())
        files = cached["files"] if cached.get("version") == _VERSION else {}
    except (OSError, ValueError, KeyError):
        files = {}
    fresh, changed = {}, False
    for root in src_dirs:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in ("__pycache__", "textual test") or part.startswith(".") for part in path.parts):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            key, stamp = str(path), [st.st_mtime, st.st_size]
            old = files.get(key)
            if old and old[0] == stamp:
                fresh[key] = old
                continue
            fresh[key] = [stamp, _file_symbols(path, _module_name(path, root))]
            changed = True
    if changed or len(fresh) != len(files):
        try:
            cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"version": _VERSION, "files": fresh}))
        except OSError:
            pass
    return Symbols([data for _stamp, data in fresh.values() if data])


class Symbols:
    """Classes, functions and modules of the scanned source, by name."""

    def __init__(self, files):
        self.classes, self.functions, self.decorators, self.modules = {}, {}, {}, {}
        for f in files:
            self.modules[f["module"]] = set(f["names"])
            for name, entry in f["classes"].items():
                self.classes.setdefault(name, []).append(entry)
            for name, sig in f["functions"].items():
                self.functions[f"{f['module']}.{name}"] = sig
            self.decorators.update(f["decorators"])
        # a package also "has" its submodules
        for module in list(self.modules):
            parent, _, child = module.rpartition(".")
            if parent in self.modules:
                self.modules[parent].add(child)

    def _mro(self, cls):
        """Every class entry `cls` stands for, with its bases; and whether
        anything in it is beyond judging."""
        seen, todo, entries, open_ = set(), [cls], [], False
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            if name in _PLAIN_BASES:
                continue
            found = self.classes.get(name)
            if not found:
                open_ = True  # a base outside the scanned source
                continue
            for e in found:
                entries.append(e)
                open_ = open_ or e["open"]
                todo += e["bases"]
                for d in e["decorators"]:
                    dec = self.decorators.get(d)
                    if dec and dec["open"]:
                        open_ = True
        return entries, open_

    def attribute(self, cls, attr):
        """(found, class of the attribute or None, signature or None);
        found is None when it cannot be judged."""
        entries, open_ = self._mro(cls)
        if not entries:
            return None, None, None
        for e in entries:
            if attr in e["methods"]:
                return True, None, e["methods"][attr]
            if attr in e["components"]:
                return True, e["components"][attr], None
            if attr in e["attrs"]:
                return True, None, None
            for d in e["decorators"]:
                dec = self.decorators.get(d)
                if dec and attr in dec["names"]:
                    return True, None, None
        return (None if open_ else False), None, None

    def members(self, cls):
        entries, _open = self._mro(cls)
        out = set()
        for e in entries:
            out.update(e["methods"], e["components"], e["attrs"])
            for d in e["decorators"]:
                out.update((self.decorators.get(d) or {}).get("names", ()))
        return sorted(n for n in out if not n.startswith("__"))

    def init_signature(self, cls):
        for e in self.classes.get(cls, ()):
            if "__init__" in e["methods"]:
                return e["methods"]["__init__"]
        return None


# -- the check -----------------------------------------------------------------------------


_MAGIC_RX = re.compile(r"^(\s*)(%|!|\?).*$|^(\s*)(.*)\?\s*$")


def _python_only(code):
    """IPython-only lines (magics, shell escapes, `obj?`) as `pass`, so the
    line numbers stay; a %%cell magic block is not Python at all."""
    if code.lstrip().startswith("%%"):
        return None
    lines = []
    for line in code.splitlines():
        line = re.sub(r"^\s*(>>>|\.\.\.) ?", "", line)
        m = _MAGIC_RX.match(line)
        lines.append(f"{m.group(1) or m.group(3) or ''}pass" if m else line)
    return "\n".join(lines)


def _resolved(obj):
    """A lazy namespace Proxy's object if it was built already, else None;
    anything else as it is. Never builds anything."""
    try:
        resolved = object.__getattribute__(obj, "__resolved__")
    except AttributeError:
        return obj
    except Exception:
        return None
    return object.__getattribute__(obj, "__wrapped__") if resolved else None


def _chain(node):
    """(root name, [attributes]) of a.b.c, or None."""
    attrs = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return node.id, attrs[::-1]
    return None


def _close(name, candidates):
    return difflib.get_close_matches(name, list(candidates), n=3, cutoff=0.6)


def _hint(name, candidates):
    """" (did you mean a, b?)", else " (it has: ...)" -- what helps both the
    reader and the model put it right."""
    candidates = [c for c in candidates if not c.startswith("_")]
    others = _close(name, candidates)
    if others:
        return f" (did you mean {', '.join(others)}?)"
    if candidates:
        shown = sorted(candidates)[:15]
        return f" (it has: {', '.join(shown)}{', ...' if len(candidates) > 15 else ''})"
    return ""


def _live_signature(obj):
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    kinds = [p.kind for p in sig.parameters.values()]
    return {
        "params": [n for n, p in sig.parameters.items() if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)],
        "varkw": inspect.Parameter.VAR_KEYWORD in kinds,
    }


class _Checker:
    def __init__(self, symbols, namespace, user_ns):
        self.symbols = symbols
        self.namespace = {name: cls for name, cls, _module, _state in namespace}
        self.user_ns = user_ns
        self.problems = []
        self.local = set()  # names the code defines itself
        self.imported = {}  # alias -> ("module", name) | ("class", name) | ("function", qualname)

    def problem(self, node, message, code=None):
        self.problems.append(Problem(getattr(node, "lineno", 0), code or ast.unparse(node)[:80], message))

    # what the code defines -----------------------------------------------------------
    def collect(self, tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                self.local.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.local.add(n.name)
                if not isinstance(n, ast.ClassDef):
                    self.local.update(a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs)
                    for a in (n.args.vararg, n.args.kwarg):
                        if a:
                            self.local.add(a.arg)
            elif isinstance(n, ast.arg):
                self.local.add(n.arg)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                self.local.add(n.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if not self.check_module(n, a.name):
                        continue  # not from the scanned source: not followed
                    if a.asname:
                        self.imported[a.asname] = ("module", a.name)
                    else:
                        root = a.name.split(".")[0]
                        self.imported[root] = ("module", root)
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                if not self.check_module(n, n.module):
                    continue
                for a in n.names:
                    alias = a.asname or a.name
                    kind = self.module_member(n.module, a.name)
                    if kind is False:
                        others = _close(a.name, self.symbols.modules.get(n.module, ()))
                        self.problem(n, f"{n.module} has no {a.name}" + (f" (did you mean {', '.join(others)}?)" if others else ""),
                                     f"from {n.module} import {a.name}")
                    elif kind:
                        self.imported[alias] = kind

    def check_module(self, node, module):
        """True if it is a module of the scanned source; reports a missing one."""
        top = module.split(".")[0]
        if not any(m == top or m.startswith(top + ".") for m in self.symbols.modules):
            return False
        if module not in self.symbols.modules:
            parent = module.rpartition(".")[0]
            others = _close(module.rpartition(".")[2], self.symbols.modules.get(parent, ()))
            self.problem(node, f"there is no module {module}" + (f" (did you mean {parent}.{others[0]}?)" if others else ""),
                         f"import {module}")
            return False
        return True

    def module_member(self, module, name):
        if f"{module}.{name}" in self.symbols.modules:
            return ("module", f"{module}.{name}")
        if name in (self.symbols.modules.get(module) or ()):
            if name in self.symbols.classes and any(e["module"] == module for e in self.symbols.classes[name]):
                return ("class", name)
            if f"{module}.{name}" in self.symbols.functions:
                return ("function", f"{module}.{name}")
            return None  # something else, defined there
        return False

    # chains ----------------------------------------------------------------------------
    def check(self, tree):
        self.collect(tree)
        seen = set()
        for node in ast.walk(tree):
            target = node.func if isinstance(node, ast.Call) else node
            if not isinstance(target, (ast.Attribute, ast.Name)) or id(target) in seen:
                continue
            ch = _chain(target)
            if ch is None:
                continue
            # only the longest chain: a.b.c, not again a.b
            for sub in ast.walk(target):
                seen.add(id(sub))
            sig = self.walk(target, *ch)
            if isinstance(node, ast.Call) and sig and not sig.get("varkw"):
                for kw in node.keywords:
                    if kw.arg and kw.arg not in sig["params"]:
                        others = _close(kw.arg, sig["params"])
                        self.problem(
                            node,
                            f"takes no keyword {kw.arg}" + (f" (did you mean {', '.join(others)}?)" if others else "")
                            + f"; its parameters: {', '.join(sig['params']) or 'none'}",
                            f"{ast.unparse(node.func)}(..., {kw.arg}=...)",
                        )

    def walk(self, node, root, attrs):
        """Follow root.attrs; report what does not exist; the signature of
        the last element if known (for keyword checks)."""
        if root in self.imported:
            kind, name = self.imported[root]
            return self.walk_static(node, kind, name, root, attrs)
        if root in self.local or root in _EXTERNAL or hasattr(builtins, root):
            return None
        if self.user_ns is not None and root in self.user_ns:
            obj = _resolved(self.user_ns[root])
            if obj is not None:
                return self.walk_live(node, obj, root, attrs)
        if root in self.namespace and self.namespace[root]:
            return self.walk_static(node, "class", self.namespace[root], root, attrs, instance=True)
        if self.user_ns is not None and root not in self.namespace and (root not in self.user_ns) and attrs:
            others = _close(root, sorted(set(self.namespace) | set(self.user_ns)))
            self.problem(node, f"{root} is not defined in this session"
                         + (f" (did you mean {', '.join(others)}?)" if others else ""), root)
        return None

    def walk_static(self, node, kind, name, shown, attrs, instance=False):
        sig = None
        for i, attr in enumerate(attrs):
            here = ".".join([shown, *attrs[:i + 1]])
            if kind == "module":
                member = self.module_member(name, attr)
                if member is False:
                    others = _close(attr, self.symbols.modules.get(name, ()))
                    self.problem(node, f"{name} has no {attr}" + (f" (did you mean {', '.join(others)}?)" if others else ""), here)
                    return None
                if member is None:
                    return None
                kind, name = member
                instance = False
                sig = self.symbols.functions.get(name) if kind == "function" else None
                continue
            if kind != "class":
                return None
            found, component, sig = self.symbols.attribute(name, attr)
            if found is False:
                self.problem(node, f"{name} has no {attr}" + _hint(attr, self.symbols.members(name)), here)
                return None
            if found is None:
                return None
            if component:
                kind, name, sig = "class", component, None
            elif i < len(attrs) - 1:
                return None  # a method or plain attribute: beyond here, unknown
        if kind == "class" and not attrs and not instance:
            init = self.symbols.init_signature(name)
            return init
        return sig

    def walk_live(self, node, obj, shown, attrs):
        sig = None
        for i, attr in enumerate(attrs):
            here = ".".join([shown, *attrs[:i + 1]])
            try:
                value = inspect.getattr_static(obj, attr)
            except AttributeError:
                if hasattr(type(obj), "__getattr__"):
                    return None  # answers more than it shows
                try:
                    names = [n for n in dir(obj) if not n.startswith("__")]
                except Exception:
                    names = []
                self.problem(node, f"{type(obj).__name__} has no {attr}" + _hint(attr, names), here)
                return None
            if isinstance(value, (staticmethod, classmethod)):
                value = value.__func__
            if inspect.isfunction(value) or inspect.isbuiltin(value) or inspect.ismethoddescriptor(value):
                if i < len(attrs) - 1:
                    return None
                sig = _live_signature(value)
                if sig and inspect.isfunction(value) and sig["params"] and sig["params"][0] in ("self", "cls"):
                    sig["params"] = sig["params"][1:]
                return sig
            if isinstance(value, property) or inspect.isdatadescriptor(value) or inspect.ismemberdescriptor(value):
                return None  # reading it could run code
            obj = _resolved(value)
            if obj is None:
                return None
        if callable(obj) and attrs:
            return _live_signature(obj)
        return None


def check(code, symbols, namespace=(), user_ns=None):
    """[Problem] of one block of Python; [] when nothing is wrong (or it is
    not Python at all). namespace: NamespaceInfo.entries(); user_ns: the
    session's names, or None outside a session."""
    source = _python_only(code)
    if source is None:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        line = (exc.text or "").strip()
        return [Problem(exc.lineno or 0, line[:80], f"is not valid Python ({exc.msg})")]
    checker = _Checker(symbols, namespace, user_ns)
    try:
        checker.check(tree)
    except RecursionError:
        pass
    seen, out = set(), []
    for p in checker.problems:
        if (p.code, p.message) not in seen:
            seen.add((p.code, p.message))
            out.append(p)
    return out


def check_answer(text, symbols, namespace=(), user_ns=None):
    """[Problem] over every Python block of an answer (```python ...```)."""
    out = []
    for block in re.findall(r"```(?:python|py|ipython)?[ \t]*\n(.*?)```", text, re.S):
        out += check(block, symbols, namespace, user_ns)
    return out


def source_dirs(index_sources):
    """The package directories to build the table from: eco and the
    configured packages (Assistant.index_sources()["src_dirs"])."""
    return [Path(d) for d in index_sources.get("src_dirs", [logs_ask.SRC_DIR]) if os.path.isdir(d)]
