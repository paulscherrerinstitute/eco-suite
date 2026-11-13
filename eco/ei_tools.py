"""eco.ei_tools -- what the %ei model may look up while it answers.

By default (config "tools", true; `%ei -q` answers without) the model gets
the usual context and may in addition call these functions, a few times
per question, before it answers:

* search(query, where) -- more of the index: docs, eco's API, commands
  people ran, logbook posts;
* describe(name) -- an eco object's components and methods with their
  parameters: `mono`, `xrd.nu`, `scans.ascan`;
* check_code(code) -- the same check its answer gets afterwards, so it can
  correct its code first;
* storage(query) -- where something is kept (the storage spreadsheet).

All of them only read. None runs code, moves anything, reads a device's
value or builds a lazy device: describe() looks at objects the session
built already (inspect.getattr_static, no property evaluated) and at
eco's source otherwise. What they return goes to the model endpoint, like
the usual context; nothing else does. Every call is kept in
Answer.tool_calls.
"""

import inspect
import json

from eco import ei_check, logs_ask

#: at most this many tool calls per question (config "tool_calls")
MAX_CALLS = 5
#: a tool's answer is cut to this many characters
MAX_RESULT = 4000

_WHERE = {
    "all": logs_ask.ALL_KINDS,
    "docs": ("doc",),
    "api": ("api",),
    "commands": ("command", "note_command"),
    "logbooks": ("scilog", "elog", "note"),
    "examples": ("example",),
}

TOOLS = [
    {"type": "function", "function": {
        "name": "search",
        "description": "Search eco's docs, API, the commands users ran and the logbooks (SciLog, elog) for words. "
                       "Use it when the context does not show what you need.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "a few words, e.g. 'mesh scan two motors'"},
            "where": {"type": "string", "enum": sorted(_WHERE), "description": "what to search (default all)"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "describe",
        "description": "Components and methods (with parameters) of an eco object of the session, by its name: "
                       "'mono', 'xrd.nu', 'scans'. Use it before writing code that uses an object.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "a dotted name as typed in the session"},
        }, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "check_code",
        "description": "Check Python code against eco as it is today (attributes, keywords, imports); it is not run. "
                       "Use it on your code before answering.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string"},
        }, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "storage",
        "description": "Where an item is kept at the beamline (cabinet and room), or what a cabinet (e.g. 'BC6') holds.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]}}},
]


class Toolbox:
    def __init__(self, assistant):
        self.asst = assistant
        self.calls = []  # [(name, arguments, result)]

    def run(self, name, arguments):
        """The text answer of one call (errors are answers too: the model
        can read them and try otherwise)."""
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError as exc:
            result = f"error: arguments are not valid JSON ({exc})"
        else:
            fn = getattr(self, f"tool_{name}", None)
            if fn is None:
                result = f"error: no tool {name!r}; there are {', '.join(t['function']['name'] for t in TOOLS)}"
            else:
                try:
                    result = fn(**args)
                except TypeError as exc:
                    result = f"error: {exc}"
                except Exception as exc:
                    result = f"error: {type(exc).__name__}: {exc}"
        result = str(result)
        if len(result) > MAX_RESULT:
            result = result[:MAX_RESULT] + "\n... (cut)"
        self.calls.append((name, arguments, result))
        return result

    # -- the tools -------------------------------------------------------------------------

    def tool_search(self, query, where="all"):
        kinds = _WHERE.get(where, logs_ask.ALL_KINDS)
        hits = logs_ask.search(query, k=8, db_path=self.asst.db_path, kinds=kinds)
        if not hits:
            return "nothing found"
        return "\n\n".join(self.asst.context_item(h) for h in hits)

    def tool_describe(self, name):
        return describe(name, self.asst.symbols(), self.asst.namespace.entries() or ei_check.static_namespace(),
                        self.asst.user_ns)

    def tool_check_code(self, code):
        problems = ei_check.check(code, self.asst.symbols(), self.asst.namespace.entries(), self.asst.user_ns)
        if not problems:
            return "no problems found: every name, attribute and keyword that could be checked exists"
        return "\n".join(str(p) for p in problems)

    def tool_storage(self, query):
        from eco.utilities import storage_locations

        st = storage_locations.load()
        loc, found = st.lookup(query)
        head = f"contents of {loc}:\n" if loc is not None else ""
        return (head + st.describe(found, limit=30)) if found else f"nothing in the storage sheet matches {query!r}"


# -- describe ----------------------------------------------------------------------------------


def _signature_text(params):
    return f"({', '.join(p for p in params if p not in ('self', 'cls'))})"


def describe(name, symbols, namespace=(), user_ns=None, limit=60):
    """Text: what `name` (dotted) is and has. Live where the session built
    it, else from eco's source; never builds or reads anything."""
    root, *attrs = [p for p in name.strip().split(".") if p]
    if not root:
        return "error: give a name"
    if user_ns is not None and root in user_ns:
        obj = ei_check._resolved(user_ns[root])
        if obj is not None:
            return _describe_live(name, obj, attrs, limit)
    classes = {n: cls for n, cls, *_ in namespace}
    if root not in classes:
        close = ei_check._close(root, list(classes) + list(user_ns or ()))
        return f"{root} is not a name of the session" + (f" (did you mean {', '.join(close)}?)" if close else "")
    cls = classes[root]
    for i, attr in enumerate(attrs):
        found, component, sig = symbols.attribute(cls, attr)
        here = ".".join([root, *attrs[:i + 1]])
        if found is False:
            return f"{'.'.join([root, *attrs[:i]])} ({cls}) has no {attr}" + ei_check._hint(attr, symbols.members(cls))
        if found is None:
            return f"{here}: cannot be told from the source ({cls} is built dynamically)"
        if component:
            cls = component
            continue
        if sig is not None and i == len(attrs) - 1:
            return f"{here}{_signature_text(sig['params'])}{' **kwargs' if sig.get('varkw') else ''}: a method of {cls}"
        return f"{here}: an attribute of {cls} (its value is not read)"
    return _describe_class(name, cls, symbols, limit)


def _describe_class(name, cls, symbols, limit):
    entries, open_ = symbols._mro(cls)
    if not entries:
        return f"{name}: {cls} (not in eco's source; nothing more known)"
    components, methods, attrs = {}, {}, set()
    for e in entries:  # the class itself first, then its bases
        for k, v in e["components"].items():
            components.setdefault(k, v)
        for k, v in e["methods"].items():
            methods.setdefault(k, v)
        attrs.update(e["attrs"])
    # what every Assembly has (status(), get_tree(), ...) would crowd out the
    # device's own members: named once instead
    generic = set()
    if cls != "Assembly":
        base = set(symbols.members("Assembly"))
        if base and base <= set(symbols.members(cls)):
            generic = base
    components = {k: v for k, v in components.items() if k not in generic}
    methods = {k: v for k, v in methods.items() if k not in generic}
    attrs -= generic
    out = [f"{name}: {cls} (from eco's source{'; it also has members added at run time' if open_ else ''})"]
    if components:
        out.append("components: " + ", ".join(f"{k} ({v})" for k, v in sorted(components.items())[:limit]))
    public = {k: v for k, v in methods.items() if not k.startswith("_")}
    if public:
        out.append("methods: " + ", ".join(f"{k}{_signature_text(v['params'])}" for k, v in sorted(public.items())[:limit]))
    plain = sorted(a for a in attrs if not a.startswith("_") and a not in components and a not in methods)
    if plain:
        out.append("attributes: " + ", ".join(plain[:limit]))
    if generic:
        out.append("plus what every Assembly has: status(), get_tree(), settings(), wm(), mv(...), memory, ...")
    return "\n".join(out)


def _describe_live(name, obj, attrs, limit):
    for i, attr in enumerate(attrs):
        here = ".".join([name.split(".")[0], *attrs[:i + 1]])
        try:
            value = inspect.getattr_static(obj, attr)
        except AttributeError:
            try:
                names = [n for n in dir(obj) if not n.startswith("_")]
            except Exception:
                names = []
            return f"{type(obj).__name__} has no {attr}" + ei_check._hint(attr, names)
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if inspect.isfunction(value) or inspect.ismethoddescriptor(value) or inspect.isbuiltin(value):
            sig = ei_check._live_signature(value)
            doc = (inspect.getdoc(value) or "").split("\n\n")[0][:300]
            return f"{here}{_signature_text(sig['params']) if sig else '(...)'}: a method of {type(obj).__name__}" + (
                f"\n{doc}" if doc else "")
        if isinstance(value, property) or inspect.isdatadescriptor(value):
            return f"{here}: a property of {type(obj).__name__} (not read: reading it may talk to hardware)"
        resolved = ei_check._resolved(value)
        if resolved is None:
            return f"{here}: not built yet in this session (it is built on first use)"
        obj = resolved
    t = type(obj)
    out = [f"{name}: {t.__name__} (in this session)"]
    doc = (inspect.getdoc(t) or "").split("\n\n")[0][:300]
    if doc:
        out.append(doc)
    try:
        items = list(vars(obj).items())
    except TypeError:
        items = []
    comps = [f"{k} ({type(v).__name__})" for k, v in items
             if not k.startswith("_") and "alias" in getattr(v, "__dict__", {})]
    if comps:
        out.append("components: " + ", ".join(comps[:limit]))
    methods = []
    for k, v in inspect.getmembers_static(t):
        if k.startswith("_"):
            continue
        if isinstance(v, (staticmethod, classmethod)):
            v = v.__func__
        if inspect.isfunction(v):
            sig = ei_check._live_signature(v)
            methods.append(f"{k}{_signature_text(sig['params']) if sig else '(...)'}")
    if methods:
        out.append("methods: " + ", ".join(methods[:limit]))
    return "\n".join(out)
