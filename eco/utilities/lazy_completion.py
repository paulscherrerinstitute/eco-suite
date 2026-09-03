"""Tab-completion confirmation gate for eco's lazy namespace components.

See CLAUDE.md's "Tab completion" section for the two other mechanisms this
sits on top of (`use_jedi = False`, and the `guarded_eval` allow-list entry
for `eco.utilities.config.Proxy`). That allow-list entry is what makes
`prepump.line1_usd.<TAB>` return real completions instead of nothing once
`prepump` has been used -- but its side effect is that completing *into* a
still-**un**resolved top-level component now silently triggers its real
(EPICS) initialisation, with no warning, just from pressing Tab. This module
adds the warning back as a two-Tab confirmation instead of turning the
allow-list fix off again:

- 1st Tab into an unresolved component: no completions, plus a printed
  "not initialized -- press Tab again" message.
- 2nd Tab (same target, any further typing in between is fine): the
  component is actually resolved and normal completion proceeds.

Implemented by wrapping `IPCompleter._attr_matches` -- the method IPython's
built-in `python_matcher` calls for any `NAME.NAME...` completion -- rather
than adding a competing custom matcher, so the exact same expression
parsing/stripping IPython itself uses (`_ATTR_MATCH_RE`,
`_strip_code_before_operator`) is reused instead of duplicated.
"""

import re

from IPython.core.completer import IPCompleter

#: Bail out (defer to IPython's normal handling, no gate) for anything other
#: than a plain dotted chain of names -- calls, subscripts, literals, etc.
#: are not what this gate is for, and re-implementing guarded_eval's own
#: safety analysis for them here would be redundant and error-prone.
_SIMPLE_DOTTED_CHAIN_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")

#: id() of every unresolved Proxy the user has already been warned about
#: once. Cleared for a given proxy the moment it is actually resolved (via
#: confirmation, or by any other code path touching it first) -- see
#: `_gated_attr_matches`. Never otherwise cleaned up, but bounded in
#: practice by the number of distinct lazy top-level components, so this
#: is not worth the complexity of e.g. a weak-value cache.
_armed_proxy_ids = set()


def _is_resolved(obj):
    """True unless `obj` is a still-unresolved eco.utilities.config.Proxy.
    Reads `__resolved__` without ever triggering resolution -- unlike a
    plain `getattr`/`hasattr` on the proxy itself, which would."""
    try:
        return object.__getattribute__(obj, "__resolved__")
    except AttributeError:
        return True


def _find_blocking_unresolved(expr, namespace, global_namespace):
    """Walk a plain dotted-name chain (already regex-validated by the
    caller) left to right using only non-resolving lookups. Returns
    (name, obj) for the first still-unresolved Proxy blocking further
    traversal (`name` being the last attribute name used to reach it), or
    None if the whole chain is resolved (or doesn't exist / isn't
    reachable this way, in which case IPython's normal handling decides
    what happens). `namespace`/`global_namespace` are the same locals/
    globals `IPCompleter._evaluate_expr` itself resolves names through."""
    parts = expr.split(".")
    name = parts[0]
    if name in namespace:
        obj = namespace[name]
    elif name in global_namespace:
        obj = global_namespace[name]
    else:
        return None

    for part in parts[1:]:
        if not _is_resolved(obj):
            return name, obj
        try:
            obj = getattr(obj, part)
        except Exception:
            return None
        name = part

    if not _is_resolved(obj):
        return name, obj
    return None


def _gated_attr_matches(self, text, include_prefix=True, context=None):
    m = self._ATTR_MATCH_RE.match(text)
    if m is not None:
        expr = m.group(1)
        try:
            expr = self._strip_code_before_operator(expr)
        except Exception:
            pass
        if _SIMPLE_DOTTED_CHAIN_RE.match(expr):
            blocking = _find_blocking_unresolved(
                expr, self.namespace, self.global_namespace
            )
            if blocking is not None:
                name, proxy = blocking
                key = id(proxy)
                if key in _armed_proxy_ids:
                    _armed_proxy_ids.discard(key)
                    try:
                        object.__getattribute__(proxy, "__wrapped__")
                    except Exception as e:
                        print(
                            f"\neco: '{name}' failed to initialize: "
                            f"{type(e).__name__}: {e}"
                        )
                        return [], ""
                    # falls through to the real completion below, now that
                    # `proxy` is resolved
                else:
                    _armed_proxy_ids.add(key)
                    print(
                        f"\neco: '{name}' is not initialized yet -- "
                        f"press Tab again to initialize it and complete."
                    )
                    return [], ""
    return _orig_attr_matches(self, text, include_prefix=include_prefix, context=context)


_orig_attr_matches = IPCompleter._attr_matches
_installed = False


def install_lazy_completion_gate():
    """Idempotently monkeypatch `IPCompleter._attr_matches` (process-wide,
    like the `use_jedi`/`guarded_eval` completion settings this pairs
    with -- see the module docstring) to add the two-Tab confirmation for
    completing into a still-unresolved lazy namespace component."""
    global _installed
    if _installed:
        return
    IPCompleter._attr_matches = _gated_attr_matches
    _installed = True
