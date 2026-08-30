"""Proof-of-concept user/group write-access control for eco adjustables.

Rationale
---------
Every write in eco funnels through a single method,
``Adjustable.set_target_value(value, hold=False) -> Changer``; every other
mutator (``mv``/``mvr``/``umv``, the ``.value`` setter, ``update_change``,
``jog``, ``tweak``, ``reset_current_value_to``, and an ``AdjustableVirtual``
driving its children) routes through it. And every ``set_target_value``
in turn builds a ``Changer(parent=self, ...)``, so a single gate in
``Changer.__init__`` covers (almost) all writes at once. This module provides
that gate (:func:`check_write`) plus the identity/ACL machinery behind it;
it is wired in centrally via ``eco.devices_general.utilities._access_gate``,
called from every ``Changer`` constructor. :func:`write_guarded` (a per-class
decorator wrapping ``set_target_value``) is kept as an alternative/finer-grained
wiring point but is not the default.

Scope - read this
-----------------
This is a **guardrail, not a security boundary**. Anyone with shell or EPICS
access bypasses it trivially (``caput``, or re-binding ``set_target_value``).
Its job is to stop honest mistakes - the laser group fat-fingering an x-ray
optics motor - and to leave an audit trail. Real "user X may not move this
motor" enforcement belongs in the IOC (EPICS Access Security ``.acf`` /
CA gateway).

Shared-account caveat
---------------------
Beamline consoles log in as one shared Unix account (``gac-bernina``), which
is simultaneously a member of *every* pgroup. So neither ``os.getuid()`` nor
``os.getgroups()`` can tell two operators apart at the console. Effective
identity therefore comes from (in priority order): an explicit
:func:`login`, the ``ECO_USER`` env var, then the POSIX user - with POSIX
groups only as a fallback. Log in under your own remote account and the POSIX
layer becomes meaningful again.

The whole mechanism is **off by default** (``enforce = False``): it audits (if
enabled) but never blocks until a beamline opts in.

Audit trail location
--------------------
:data:`AUDIT_PATH` defaults to a *per-user* file in the OS temp dir, because a
single fixed name under ``/tmp`` is unusable across the accounts that share a
console - see the comment there. Set ``ECO_ACCESS_AUDIT`` to a path in a
setgid, group-writable directory on the beamline share for one trail shared by
everyone.
"""

import getpass
import json
import logging
import os
import threading
import time
from pathlib import Path

from ..utilities.tempfiles import user_temp_path

logger = logging.getLogger(__name__)


class AccessDenied(PermissionError):
    """Raised (synchronously, before any Changer is built) when the current
    identity is not permitted to write to an adjustable."""


# --- module-level policy switches (a beamline flips these on) ----------------

# Master switch. While False, check_write() never raises - it only audits.
enforce = False
# Append a line to the audit log for every write attempt (allowed or denied).
audit = True
# What to do when NO acl rule matches a name at all: "allow" or "deny".
default_policy = "allow"

# Audit-sink state, managed by _log_audit(): the path actually being appended
# to, the AUDIT_PATH it was derived from (so a reconfigured AUDIT_PATH is
# picked up), and whether file auditing gave up for this session.
_audit_path = None
_audit_source = None
_audit_disabled = False

# Path to the JSON acl file and the audit log. Overridable by env for testing.
ACL_PATH = Path(os.environ.get("ECO_ACL_PATH", Path.home() / ".eco_access_acl.json"))
# The audit trail defaults to a *per-user* file in the OS temp dir. Several
# POSIX accounts share the beamline consoles, and one fixed name under /tmp
# cannot be appended to by a second account at all: /tmp is world-writable and
# sticky, so fs.protected_regular (on by default on RHEL 9) refuses to open
# another user's file there for writing no matter how permissive the mode bits
# are. For one trail shared by all accounts, point ECO_ACCESS_AUDIT at a
# setgid, group-writable directory outside /tmp (e.g.
# /sf/bernina/config/eco/log/access_audit.log), where the ownership problem
# does not arise.
AUDIT_PATH = Path(
    os.environ.get("ECO_ACCESS_AUDIT") or user_temp_path("eco_access_audit.log")
)


# --- identity ----------------------------------------------------------------


class Identity:
    """The effective actor for the current session: a display name and the set
    of groups they act with. See module docstring on why this is not simply
    ``os.getuid()``/``os.getgroups()``."""

    def __init__(self, name, groups):
        self.name = name
        self.groups = set(groups)

    def __repr__(self):
        return f"<Identity {self.name} groups={sorted(self.groups)}>"


# Session override set by login(); highest-priority identity source.
_session_identity = None
_lock = threading.Lock()


def _posix_groups(username=None):
    """POSIX groups of `username` (defaults to the process user). On the shared
    account this is the union of all pgroups - see module docstring."""
    import grp
    import pwd

    try:
        if username is None:
            gids = os.getgroups()
            names = set()
            for gid in gids:
                try:
                    names.add(grp.getgrgid(gid).gr_name)
                except KeyError:
                    pass
            return names
        pw = pwd.getpwnam(username)
        primary = grp.getgrgid(pw.pw_gid).gr_name
        secondary = {g.gr_name for g in grp.getgrall() if username in g.gr_mem}
        return {primary} | secondary
    except Exception as e:  # never let identity resolution kill a write path
        logger.warning(f"could not resolve posix groups: {e}")
        return set()


def login(name, groups=None):
    """Declare who is driving this eco session (the answer POSIX can't give on
    the shared account). If `groups` is None, the POSIX groups of `name` are
    looked up; pass an explicit set to act with exactly those."""
    global _session_identity
    if groups is None:
        groups = _posix_groups(name)
    with _lock:
        _session_identity = Identity(name, groups)
    logger.info(f"eco access: logged in as {_session_identity}")
    return _session_identity


def logout():
    global _session_identity
    with _lock:
        _session_identity = None


def current_identity():
    """Resolve the effective identity: explicit login() > $ECO_USER > POSIX
    user. Groups come from the login() override if given, otherwise the POSIX
    groups of the resolved user name."""
    if _session_identity is not None:
        return _session_identity
    env_user = os.environ.get("ECO_USER")
    if env_user:
        return Identity(env_user, _posix_groups(env_user))
    try:
        name = getpass.getuser()
    except Exception:
        name = "unknown"
    return Identity(name, _posix_groups())


# --- access control list -----------------------------------------------------


class AccessList:
    """Longest-prefix ACL over the dotted adjustable namespace.

    A rule is ``{"prefix": <dotted>, "allow": [<group>, ...]}``. A rule with
    prefix ``P`` matches an adjustable whose full name is ``P`` itself or any
    descendant (``P.something...``); ``prefix: ""`` is the root and matches
    everything. For a given name the **longest** matching prefix wins, so a
    rule on ``bernina.laser`` covers the whole laser subtree while a deeper
    rule on ``bernina.laser.delay`` overrides just that branch - this is how
    the assembly hierarchy is reused for access management. ``"*"`` in an
    ``allow`` list means "any group".
    """

    def __init__(self, rules=None):
        self.rules = list(rules or [])

    @classmethod
    def load(cls, path=None):
        path = Path(path or ACL_PATH)
        if not path.exists():
            return cls([])
        with path.open("r") as f:
            return cls(json.load(f))

    def store(self, path=None):
        path = Path(path or ACL_PATH)
        with path.open("w") as f:
            json.dump(self.rules, f, indent=2)

    def matching_rule(self, full_name):
        """Return the winning (longest-prefix) rule for `full_name`, or None."""
        best = None
        best_len = -1
        for rule in self.rules:
            prefix = rule.get("prefix", "")
            if prefix == "" or full_name == prefix or full_name.startswith(prefix + "."):
                if len(prefix) > best_len:
                    best, best_len = rule, len(prefix)
        return best

    def is_allowed(self, full_name, groups):
        """(allowed: bool, reason: str) for `groups` writing `full_name`."""
        rule = self.matching_rule(full_name)
        if rule is None:
            allowed = default_policy == "allow"
            return allowed, f"no rule (default_policy={default_policy})"
        allow = set(rule.get("allow", []))
        if "*" in allow:
            return True, f"prefix '{rule['prefix']}' allows *"
        ok = bool(allow & set(groups))
        return ok, f"prefix '{rule['prefix']}' allows {sorted(allow)}"


# Process-wide ACL, lazily loaded from ACL_PATH on first use. Swap freely in
# tests / at runtime: access.acl = AccessList([...]).
acl = None


def get_acl():
    global acl
    if acl is None:
        acl = AccessList.load()
    return acl


# --- the gate ----------------------------------------------------------------


def _full_name(adjustable):
    try:
        return adjustable.alias.get_full_name()
    except Exception:
        return getattr(adjustable, "name", repr(adjustable))


def _append_audit_line(path, line):
    is_new = not path.exists()
    with path.open("a") as f:
        f.write(line)
    if is_new:
        try:
            # umask would otherwise leave this owner-writable only, and a
            # shared trail is written by several POSIX accounts.
            path.chmod(0o664)
        except OSError:
            # Someone else created it in the meantime; their bits stand.
            pass


def _log_audit(identity, full_name, allowed, reason, blocked):
    """Append one line to the audit trail, degrading quietly.

    A write gate runs on *every* set_target_value, so an unwritable trail must
    not warn once per motor move: the first failure falls back to the per-user
    temp path (warning once), and if that fails too, file auditing switches
    itself off for the session.
    """
    global _audit_path, _audit_source, _audit_disabled
    if not audit:
        return
    if AUDIT_PATH != _audit_source:
        # AUDIT_PATH was (re)configured since the last write - start over.
        _audit_source, _audit_path, _audit_disabled = AUDIT_PATH, AUDIT_PATH, False
    if _audit_disabled:
        return
    line = (
        f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{identity.name}\t"
        f"groups={sorted(identity.groups)}\t{full_name}\t"
        f"{'ALLOW' if allowed else 'DENY'}\tblocked={blocked}\t{reason}\n"
    )
    try:
        _append_audit_line(_audit_path, line)
        return
    except Exception as e:
        failure = e
    fallback = Path(user_temp_path("eco_access_audit.log"))
    if _audit_path != fallback:
        logger.warning(
            f"could not write access audit line to {_audit_path} ({failure}); "
            f"falling back to {fallback}"
        )
        _audit_path = fallback
        try:
            _append_audit_line(_audit_path, line)
            return
        except Exception as e:
            failure = e
    _audit_disabled = True
    logger.warning(
        f"access audit logging disabled for this session: could not write "
        f"{_audit_path}: {failure}"
    )


def check_write(adjustable):
    """Gate a write to `adjustable`. Raises :class:`AccessDenied` when the
    current identity is not allowed AND ``enforce`` is True; otherwise returns
    quietly. Always audits (when ``audit``). Called at the top of every guarded
    ``set_target_value`` - fail-fast, synchronous, before the Changer exists."""
    identity = current_identity()
    full_name = _full_name(adjustable)
    allowed, reason = get_acl().is_allowed(full_name, identity.groups)
    blocked = (not allowed) and enforce
    _log_audit(identity, full_name, allowed, reason, blocked)
    if blocked:
        raise AccessDenied(
            f"{identity.name} (groups {sorted(identity.groups)}) may not write "
            f"'{full_name}': {reason}."
        )
    if not allowed:
        # enforcement off: warn but let it through, so a beamline can watch the
        # audit log to tune the ACL before turning enforce on.
        logger.warning(
            f"[access, not enforced] would deny {identity.name} -> {full_name}: {reason}"
        )


# --- wiring decorator --------------------------------------------------------


def write_guarded(Adj):
    """Class decorator: wrap ``Adj.set_target_value`` so it calls
    :func:`check_write` first. Add it to an adjustable's decorator stack (see
    ``eco.elements.adjustable``). Idempotent - re-decorating is a no-op."""
    orig = Adj.__dict__.get("set_target_value", None)
    if orig is None or getattr(orig, "_access_guarded", False):
        return Adj

    def set_target_value(self, value, *args, **kwargs):
        check_write(self)
        return orig(self, value, *args, **kwargs)

    set_target_value._access_guarded = True
    set_target_value.__wrapped__ = orig
    Adj.set_target_value = set_target_value
    return Adj
