"""Group-writable creation of everything eco writes to a shared beamline tree.

Two kinds of tree are covered, for the same reason: whichever account happens
to run the session -- usually the shared ``gac-bernina`` console account,
sometimes a personal one -- writes files that *every other member of the group*
has to be able to rewrite afterwards.

* **Results**, below ``/sf/<instrument>/data/<pgroup>/res``: ``run_data`` and
  everything in it -- per-run ``aux`` json, run tables, scan info, memories,
  pedestal/gainmap copies. The group here is the **pgroup** (`pgroup_of_path`).
* **Shared configuration and state**, below ``/sf/<instrument>/code/<account>/``
  and friends: the ``eco_cnf_bernina`` memory/preset/offset/configuration trees,
  the namespace alias json inside the checkout itself. There is no pgroup in
  those paths, so the group is taken from **the enclosing directory**
  (`target_group_of_path`) -- what setgid would have propagated.

Getting that right needs two things that are *not* the default:

* every directory group-writable **and setgid** (``0o2775``) -- the write bit so
  another group member can create entries in it, the setgid bit so whatever
  they create inherits the directory's group rather than their own primary one;
* every file group-writable (``0o664``).

Neither happens on its own. ``mkdir(mode=0o775)`` is masked by the process
umask (``0022`` on the beamline consoles), which strips exactly the group-write
bit, and ``open(path, "w")`` creates ``0o644`` for the same reason -- so eco's
own output used to come out as::

    drwxr-sr-x. gac-bernina p23415   run_data/          <- group cannot create runs
    -rw-r--r--. gac-bernina p23415   aux/status.json    <- group cannot rewrite

The pgroup itself is inherited correctly *as long as the setgid bit survives*,
which is why the bare ``chmod(0o775)`` that used to follow these mkdirs was
worse than nothing: it cleared setgid, so files created afterwards landed with
the creator's primary group instead of the pgroup::

    -rw-r--r--. gac-bernina unx-nogroup  p23415_runtable.pkl

Hence `DIR_MODE` carries `stat.S_ISGID`, and every chmod here *adds* bits to
the mode already on disk instead of replacing it -- a directory an admin made
more permissive stays that way.

The same failure in its other form is what motivated `target_group_of_path`:
``eco_cnf_bernina/memory`` is ``unx-sf_bernina_bs`` but was never setgid, so
263 of the 1505 device directories under it -- every one created on a day the
shared account happened to touch it first -- came out ``unx-nogroup`` with
``0644`` files, unwritable by anyone else on the beamline. eco could not have
noticed: outside a pgroup tree it used to skip the group question entirely.

Nothing here fights a site-managed setup: permissions are only touched when
they are actually missing, and a path this process cannot fix is reported once
(see `warn_once`) **naming the account that owns it** -- on a shared tree that
account is the only one besides root who can fix it -- rather than raised;
losing a run because a chmod failed would be far worse than the wrong mode.
Reporting deduplicates on the *parent* directory, so a tree of a few hundred
identically-broken siblings costs one warning, not a few hundred, and the
suggested command is recursive on that parent for the same reason. Before
warning, POSIX ACLs are consulted (`acl_grants_group_write`), so a tree where
group write is already granted by an ACL rather than by the mode bits stays
quiet, and a bare missing setgid bit on an otherwise correct directory is not
reported at all -- it blocks nobody, and eco sets the group explicitly on
everything it creates anyway.

`repair_tree` is the counterpart for the account that *does* own such a tree:
it fixes everything it can and lists the owner of everything it cannot.

Stdlib-only (no eco/EPICS/GUI imports), so it is safe to import from any code
path.
"""

import grp
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

#: Directories: ``rwxrwsr-x``. Group write so other pgroup members can create
#: entries, setgid so those entries inherit the pgroup.
DIR_MODE = 0o2775

#: Files: ``rw-rw-r--``.
FILE_MODE = 0o664

_DIR_BITS = stat.S_ISGID | stat.S_IRWXG
_FILE_BITS = stat.S_IRGRP | stat.S_IWGRP

_PGROUP_RE = re.compile(r"^p\d{4,}$")

# Paths already reported, so a per-run/per-step write path cannot turn one
# unfixable directory into a wall of identical warnings.
_warned = set()


def warn_once(key, message):
    """Print `message` once per process for `key`; True if it was printed."""
    if key in _warned:
        return False
    _warned.add(key)
    print(f"eco: WARNING: {message}")
    return True


@lru_cache(maxsize=None)
def _gid_of(group_name):
    """gid for a group name, or None. Cached: on a resolved-by-LDAP setup this
    is a network round trip, and it is called on every data file eco writes."""
    try:
        return grp.getgrnam(group_name).gr_gid
    except (KeyError, OSError):
        return None


@lru_cache(maxsize=None)
def _name_of_gid(gid):
    try:
        return grp.getgrgid(gid).gr_name
    except (KeyError, OSError):
        return str(gid)


_NOGROUP_RE = re.compile(r"(?:^|[-_])nogroup$", re.IGNORECASE)


def _is_nogroup_sentinel(gid):
    """True if `gid` names a ``nogroup``-shaped group (``nogroup``,
    ``unx-nogroup``, ...) -- the fallback primary group a personal account's
    files land in under a directory that lost its setgid bit, never a group
    anyone chose on purpose. See `target_group_of_path`."""
    return bool(_NOGROUP_RE.search(_name_of_gid(gid)))


def pgroup_of_path(path):
    """The pgroup owning `path`, or None if it isn't inside a pgroup tree.

    Taken from the ``pNNNNN`` component of the path rather than from whatever
    the caller thinks the current pgroup is: the point is to match the tree
    actually being written to (a run recovered into last week's pgroup must
    get *that* group, not the session's).

    `realpath` is the fallback because two of the three ways this path is
    spelled hide the pgroup behind a symlink: ``/sf/bernina/exp/<name>`` points
    at ``/sf/bernina/data/pNNNNN``, and ``<pgroup>/res`` itself points into
    ``/gpfs/photonics/swissfel/res/<instrument>/pNNNNN``. Both resolved forms
    still carry the ``pNNNNN`` component, so resolving is enough; no assumption
    about the layout around it is needed.
    """
    for candidate in (path, os.path.realpath(path)):
        for part in Path(candidate).parts:
            if _PGROUP_RE.match(part) and _gid_of(part) is not None:
                return part
    return None


@lru_cache(maxsize=None)
def _process_gids():
    """Every gid this process could chgrp *to* -- chgrp to a group you are not
    a member of is EPERM for anyone but root, so a group outside this set is
    not a target eco can propose."""
    return frozenset(os.getgroups()) | {os.getgid()}


def _current_user():
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError, ImportError):
        return str(os.geteuid())


def target_group_of_path(path):
    """The group `path` should belong to, or None if eco should not pick one.

    Inside a pgroup tree that is the pgroup (see `pgroup_of_path`). Everywhere
    else -- notably the shared config/checkout trees under
    ``/sf/<instrument>/code/<account>/`` -- it is **the group of the enclosing
    directory**, i.e. exactly what the setgid bit would have propagated had it
    been set. That fallback is the whole reason this exists: the memory tree
    under ``eco_cnf_bernina/memory`` is ``unx-sf_bernina_bs`` but was never
    setgid, so every device directory created there by the shared console
    account came out owned by *that account's primary group*
    (``unx-nogroup``), with files at ``0644`` -- unwritable by anyone else on
    the beamline. Before this, eco skipped the group entirely outside a pgroup
    tree and so never noticed.

    Only the **nearest existing ancestor directory** is consulted, and only if
    this process is a member of its group: a grandparent's group is not the
    local convention, and a group we are not in cannot be set anyway.

    One exception: if that nearest ancestor's group is itself a ``nogroup``
    sentinel, one further level up is checked for a real group before falling
    back to it. ``nogroup`` is never a deliberately-chosen shared group -- it
    is the literal symptom of the very corruption this function exists to stop
    propagating (see above): a directory that a personal account happened to
    create under a non-setgid parent lands owned by that account's own
    ``nogroup``-shaped primary group. Accepting it as "the local convention"
    would keep spreading it onto every new sibling/child written next to the
    already-broken directory -- and would do so silently, since most accounts
    on this beamline are themselves members of ``unx-nogroup``, so the
    ordinary "not a member" guard above never catches it. The one-level
    lookup recovers the real, intentionally-set group directly above the
    broken directory (e.g. ``eco_cnf_bernina/memory`` itself, one level above
    a device directory that lost it) -- exactly the group
    `ensure_group_writable`/`repair_tree` are trying to restore on that
    directory anyway. It deliberately does not walk further than one extra
    level: a tree that is genuinely, uniformly ``nogroup`` all the way up (an
    ordinary personal ``/tmp``, say) keeps exactly its previous behaviour.
    """
    pgroup = pgroup_of_path(path)
    if pgroup is not None:
        return pgroup

    ancestors = list(Path(path).absolute().parents)
    for idx, parent in enumerate(ancestors):
        try:
            st = os.stat(parent)
        except OSError:
            continue  # does not exist yet (mkdir -p is about to create it)
        if not stat.S_ISDIR(st.st_mode):
            continue
        if _is_nogroup_sentinel(st.st_gid):
            better = _real_group_one_level_up(ancestors[idx + 1 :])
            if better is not None:
                return better
        if st.st_gid in _process_gids():
            return _name_of_gid(st.st_gid)
        return None
    return None


def _real_group_one_level_up(remaining_ancestors):
    """The group of the nearest existing, non-``nogroup`` directory in
    `remaining_ancestors` that this process is a member of, or None.

    Only consulted from `target_group_of_path` when the nearest ancestor is
    itself a `nogroup` sentinel -- see there for why a single extra level is
    enough.
    """
    if not remaining_ancestors:
        return None
    try:
        st = os.stat(remaining_ancestors[0])
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    if _is_nogroup_sentinel(st.st_gid):
        return None
    if st.st_gid in _process_gids():
        return _name_of_gid(st.st_gid)
    return None


def acl_grants_group_write(path, group=None):
    """Best-effort "is group write already granted by a POSIX ACL?".

    Only consulted before warning about a path this process could not chmod,
    so a false negative costs a spurious warning and a false positive costs a
    missing one -- neither breaks anything. Shelling out to ``getfacl`` (rather
    than adding a ``pylibacl`` dependency) is fine at that rate.

    ``group::`` entries are deliberately ignored: those *are* the mode bits we
    just found lacking. What matters here is a named ``group:<pgroup>:`` entry,
    or a ``default:`` entry that will grant group write to whatever gets
    created inside. Both are masked by ``mask::``, so the mask is applied too.
    """
    try:
        out = subprocess.run(
            ["getfacl", "-c", "--absolute-names", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False

    access_mask_w = default_mask_w = True
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("mask::"):
            access_mask_w = "w" in line.split(":")[-1]
        elif line.startswith("default:mask::"):
            default_mask_w = "w" in line.split(":")[-1]

    for line in out.splitlines():
        line = line.strip()
        is_default = line.startswith("default:")
        entry = line[len("default:") :] if is_default else line
        if not entry.startswith("group:"):
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        _, name, perms = parts
        if not name:  # `group::` -- the mode bits, not an extra grant
            continue
        if group is not None and name != group:
            continue
        if "w" in perms and (default_mask_w if is_default else access_mask_w):
            return True
    return False


def _owner_name(st):
    try:
        import pwd

        return pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, OSError, ImportError):
        return str(st.st_uid)


def _ensure_bits(path, bits, warn=True):
    """Add `bits` to `path`'s mode if missing. True if the path ends up with
    them (or already had them), False if it could not be fixed.

    Reporting is left to `ensure_group_writable`, which knows about the group
    half too and so can say what is wrong in one message instead of two.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        if warn:
            warn_once(("stat", str(path)), f"cannot stat {path}: {exc}")
        return False

    if st.st_mode & bits == bits:
        return True

    try:
        # OR, never assign: an admin-widened mode (or an extra sticky/exec bit)
        # must survive eco tightening nothing it did not intend to.
        os.chmod(path, stat.S_IMODE(st.st_mode) | bits)
        return True
    except OSError:
        return False


def _ensure_group(path, group=None, warn=True):
    """Make `path` belong to `group` (default `target_group_of_path`). True if
    it does, or if there is no group eco should be picking for this path."""
    group = group or target_group_of_path(path)
    if group is None:
        return True
    gid = _gid_of(group)
    if gid is None:
        return True

    try:
        st = os.stat(path)
    except OSError:
        return False
    if st.st_gid == gid:
        return True

    try:
        os.chown(path, -1, gid)
        return True
    except OSError:
        return False


def _describe_problems(st, group, is_dir):
    """``(problems, serious)`` for `st`: what is wrong in human-readable form,
    and whether any of it actually blocks another account *now*.

    Wrong group comes first because that is the damage. A missing setgid bit is
    listed but is not on its own serious: it does not stop anyone writing here,
    it only means entries created inside would get their creator's primary
    group -- and eco sets the group explicitly on everything it creates anyway.
    Reporting it alone would mean a warning for every directory in a large,
    perfectly writable tree that simply never had the bit, which is noise; it
    is worth saying only alongside the breakage it caused.
    """
    problems = []
    serious = False
    mode = stat.S_IMODE(st.st_mode)
    gid = _gid_of(group) if group else None
    if gid is not None and st.st_gid != gid:
        problems.append(f"group {_name_of_gid(st.st_gid)} (should be {group})")
        serious = True
    bits = _DIR_BITS if is_dir else _FILE_BITS
    if st.st_mode & _FILE_BITS != _FILE_BITS:
        problems.append(f"not group-writable (mode {mode:04o})")
        serious = True
    elif st.st_mode & bits != bits:
        problems.append(f"setgid bit missing (mode {mode:04o})")
    return problems, serious


def _warn_unfixable(path, group):
    """Report, once per (problem, parent, owner), a path eco could not make
    group-writable -- naming the account that owns it, since on a shared
    beamline tree that account is the only one (besides root) who can fix it.

    Deduping on the *parent* rather than the path is what keeps a tree of a few
    hundred sibling directories with the same owner and the same problem from
    printing a few hundred identical warnings; the suggested command is
    recursive on that parent for the same reason.
    """
    try:
        st = os.stat(path)
    except OSError:
        return False
    is_dir = stat.S_ISDIR(st.st_mode)

    if acl_grants_group_write(path, group):
        return True  # already handled by an ACL, mode bits are irrelevant

    problems, serious = _describe_problems(st, group, is_dir)
    if not problems:
        return True
    if not serious:
        return False  # latent only (a bare missing setgid) -- not worth a line

    owner = _owner_name(st)
    me = _current_user()
    parent = Path(path).parent
    kind = "directory" if is_dir else "file"
    for_group = f" for {group}" if group else ""
    by = (
        f"it is owned by {owner}, and this session runs as {me}"
        if owner != me
        else f"the filesystem refused it even though {me} owns it"
    )
    fix_target = parent if is_dir else path
    fix = f"chmod -R g+rwXs '{fix_target}'"
    if group:
        fix = f"chgrp -R {group} '{fix_target}' && " + fix

    warn_once(
        ("perm", tuple(problems), str(parent), owner),
        f"cannot make {kind} {path} group-writable{for_group}: "
        f"{', '.join(problems)} -- {by}.\n"
        f"  Other members of {group or 'the group'} will not be able to "
        f"rewrite what eco stores there.\n"
        f"  {owner} can fix this (and any sibling with the same problem) "
        f"with: {fix}",
    )
    return False


def ensure_group_writable(path, pgroup=None, warn=True):
    """Make one existing file or directory group-writable and group-owned.

    Returns True if `path` ends up both, False if something could not be fixed
    (already reported via `warn_once` unless ``warn=False``).
    """
    path = Path(path)
    group = pgroup or target_group_of_path(path)
    try:
        is_dir = path.is_dir()
    except OSError:
        return False
    ok_group = _ensure_group(path, group=group, warn=warn)
    ok_mode = _ensure_bits(path, _DIR_BITS if is_dir else _FILE_BITS, warn=warn)
    if ok_group and ok_mode:
        return True
    if not warn:
        return False
    # One message covering both halves, rather than one per failed syscall.
    return _warn_unfixable(path, group)


def _pgroup_root(path, pgroup):
    """The ``.../<pgroup>`` ancestor of `path`, i.e. the level above which the
    tree is facility-managed and none of eco's business to chmod or report."""
    for parent in Path(path).parents:
        if parent.name == pgroup:
            return parent
    return None


def ensure_dir(path, pgroup=None, warn=True, check_parents=True):
    """`mkdir -p` for a results directory, group-writable at every level.

    Replaces ``p.mkdir(parents=True, exist_ok=True); p.chmod(0o775)``, which
    got both halves wrong: the mode is applied to *each* level (`parents=True`
    silently created intermediate directories at plain ``0o755``, so nobody
    else could add the next run to them) and it carries the setgid bit that a
    plain ``0o775`` chmod strips.

    `check_parents` additionally verifies the existing ancestors up to (but
    excluding) the ``<pgroup>`` directory itself -- those are the ones a
    previous eco version left at ``0o755``, and a run directory nobody else can
    create is just as blocking as a file nobody else can write. Verification is
    a stat per level, and each unfixable level is reported once per process.

    Returns the `Path` (created or not) so it can be used inline.
    """
    path = Path(path)
    pgroup = pgroup or pgroup_of_path(path)

    # Which levels are ours to fix: everything mkdir is about to create.
    created = []
    probe = path
    while not probe.exists():
        created.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if warn:
            warn_once(("mkdir", str(path)), f"could not create {path}: {exc}")
        return path

    for level in reversed(created):
        ensure_group_writable(level, pgroup=pgroup, warn=warn)

    if not created:  # pre-existing target still has to be right
        ensure_group_writable(path, pgroup=pgroup, warn=warn)

    if check_parents:
        for parent in _levels_below_pgroup_root(path)[1:]:
            if parent not in created:
                ensure_group_writable(parent, pgroup=pgroup, warn=warn)

    return path


def _levels_below_pgroup_root(path):
    """`path` and its ancestors, stopping *below* the ``<pgroup>`` directory.

    The walk is bounded on purpose: above that level (``/sf/<instrument>/data``,
    ``/gpfs/...``) the tree is facility-managed and root-owned, so chmod'ing it
    would fail and reporting it would be noise about something no beamline user
    can or should fix. Returns just `[path]` when the path is not inside a
    recognizable pgroup tree, so an unexpected layout can never send this
    walking up to ``/``.
    """
    path = Path(path)
    pgroup = pgroup_of_path(path)
    root = _pgroup_root(path, pgroup) if pgroup else None
    if root is None:
        return [path]
    levels = [path]
    for parent in path.parents:
        if parent == root or root not in parent.parents:
            break
        levels.append(parent)
    return levels


@contextmanager
def open_group_writable(path, mode="w", pgroup=None, warn=True, **kwargs):
    """`open()` for a results file, leaving it group-writable and group-owned.

    The parent directory is created (via `ensure_dir`) if missing, and the
    permissions are applied on the *open descriptor*, so they land on the file
    this call actually wrote even if it is renamed afterwards.

    An existing file that is already group-writable is left alone -- notably it
    is not an error to be unable to chmod a file another account created, as
    long as the group can write it, which is the whole point.

    If the file exists but *is not* group-writable and belongs to another
    account, a truncating open would simply fail: ``open(path, "w")`` needs
    write permission on the file itself, which a ``0644`` file owned by the
    shared console account does not give anyone else. When the directory is
    writable, the file is replaced instead (write a sibling temporary, then
    `os.replace`) -- the same thing ``mv`` would do, licensed by the same
    directory permission -- so a second account can still store its value, and
    the replacement lands with the right group and mode. Reported once, since
    silently dropping someone else's file ownership should be visible.
    """
    path = Path(path)
    if not path.parent.exists():
        ensure_dir(path.parent, pgroup=pgroup, warn=warn)

    try:
        fh = open(path, mode, **kwargs)
    except PermissionError as exc:
        tmp_path, fh = _replacement_for(path, mode, exc, warn=warn, **kwargs)
        if fh is None:
            raise
        try:
            with fh:
                yield fh
                _fix_open_file(fh, path, pgroup=pgroup, warn=warn)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return

    with fh:
        try:
            yield fh
        finally:
            _fix_open_file(fh, path, pgroup=pgroup, warn=warn)


#: Modes that discard the previous contents outright -- the only ones for which
#: replacing the file is equivalent to writing it. ``a``/``r+`` need the old
#: bytes, so a refused open there is a genuine error.
_TRUNCATING_MODES = {"w", "wb", "wt", "bw", "tw"}


def _replacement_for(path, mode, exc, warn=True, **kwargs):
    """``(tmp_path, handle)`` for rewriting `path` by replacement, or
    ``(None, None)`` when that is not applicable and the caller should re-raise.
    """
    if mode not in _TRUNCATING_MODES or not path.exists():
        return None, None
    if not os.access(path.parent, os.W_OK | os.X_OK):
        return None, None

    try:
        st = os.stat(path)
    except OSError:
        return None, None

    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".eco-tmp"
        )
    except OSError:
        return None, None

    try:
        os.fchmod(fd, FILE_MODE)  # ours, brand new: set it outright
        fh = os.fdopen(fd, mode, **kwargs)
    except OSError:
        os.close(fd)
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return None, None

    if warn:
        warn_once(
            ("replace", str(path.parent), _owner_name(st)),
            f"{path} (mode {stat.S_IMODE(st.st_mode):04o}, owner "
            f"{_owner_name(st)}) is not writable by this session "
            f"({_current_user()}): {exc.strerror}. Replacing it with a "
            f"group-writable copy so the value can still be stored -- "
            f"{_owner_name(st)} should run: "
            f"chmod -R g+rwXs '{path.parent}'",
        )
    return tmp_name, fh


def _fix_open_file(fh, path, pgroup=None, warn=True):
    """Apply the file mode/group to an open descriptor, best effort."""
    try:
        fd = fh.fileno()
        st = os.fstat(fd)
    except (OSError, ValueError, AttributeError):
        return

    group = pgroup or target_group_of_path(path)
    gid = _gid_of(group) if group else None
    if gid is not None and st.st_gid != gid:
        try:
            os.fchown(fd, -1, gid)
        except OSError:
            _ensure_group(path, group=group, warn=warn)

    if st.st_mode & _FILE_BITS != _FILE_BITS:
        try:
            os.fchmod(fd, stat.S_IMODE(st.st_mode) | _FILE_BITS)
        except OSError:
            if not _ensure_bits(path, _FILE_BITS, warn=warn) and warn:
                _warn_unfixable(path, group)


def repair_tree(path, warn=True):
    """Make everything at and below `path` group-writable and group-owned.

    The counterpart to the warnings above, for the account that owns the tree:
    ``eco.utilities.datafiles.repair_tree(
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/memory")``. Entries this
    account cannot fix are returned (and listed, owner first) rather than
    raising, so a tree with a few stragglers from a third account still gets
    everything else repaired.
    """
    path = Path(path)
    bad = []
    entries = [path]
    for root, dirs, files in os.walk(path):
        entries.extend(Path(root) / name for name in dirs + files)
    for entry in entries:
        if not ensure_group_writable(entry, warn=False):
            bad.append(entry)
    if warn and bad:
        print(
            f"eco: {len(bad)} of {len(entries)} entries under {path} could not "
            f"be fixed by {_current_user()}; their owners have to:"
        )
        for entry in bad:
            try:
                st = os.stat(entry)
            except OSError:
                continue
            print(
                f"  {stat.filemode(st.st_mode)} {_owner_name(st):>14s} "
                f"{_name_of_gid(st.st_gid):>18s}  {entry}"
            )
    return bad


def check_group_writable(path, warn=True):
    """Report (without changing anything) whether `path` and its ancestors up
    to the pgroup root are writable by the pgroup.

    Meant for interactive use -- ``eco.utilities.datafiles.check_group_writable(
    "/sf/bernina/data/p23415/res/run_data")`` -- when a collaborator says they
    cannot write somewhere. Returns the list of offending paths, empty if all
    is well.
    """
    path = Path(path)
    bad = []
    for level in _levels_below_pgroup_root(path):
        try:
            st = os.stat(level)
        except OSError:
            continue
        group = target_group_of_path(level)
        gid = _gid_of(group) if group else None
        bits = _DIR_BITS if stat.S_ISDIR(st.st_mode) else _FILE_BITS
        if st.st_mode & bits == bits and (gid is None or st.st_gid == gid):
            continue
        if acl_grants_group_write(level, group):
            continue
        bad.append(level)
        if warn:
            print(
                f"  {stat.filemode(st.st_mode)} {_owner_name(st):>14s} "
                f"{_name_of_gid(st.st_gid):>14s}  {level}"
            )
    return bad
