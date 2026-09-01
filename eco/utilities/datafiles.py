"""Group-writable creation of experiment result files under ``.../<pgroup>/res``.

Everything eco writes below ``/sf/<instrument>/data/<pgroup>/res`` (``run_data``
and everything in it -- per-run ``aux`` json, run tables, scan info, memories,
pedestal/gainmap copies) is written by whichever account happens to run the
session: usually the shared ``gac-bernina`` console account, sometimes a
personal one. Everyone else in the pgroup has to be able to add to and rewrite
those results afterwards, which needs two things that are *not* the default:

* every directory group-writable **and setgid** (``0o2775``) -- the write bit so
  another pgroup member can create entries in it, the setgid bit so whatever
  they create inherits the pgroup rather than their own primary group;
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

Nothing here fights a site-managed setup: permissions are only touched when
they are actually missing, and a path this process cannot fix is reported once
(see `warn_once`) with the command that would fix it, rather than raised --
losing a run because a chmod failed would be far worse than the wrong mode.
Before warning, POSIX ACLs are consulted (`acl_grants_group_write`), so a tree
where group write is already granted by an ACL rather than by the mode bits
stays quiet.

Stdlib-only (no eco/EPICS/GUI imports), so it is safe to import from any code
path.
"""

import grp
import os
import re
import stat
import subprocess
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


def _ensure_bits(path, bits, warn=True):
    """Add `bits` to `path`'s mode if missing. True if the path ends up with
    them (or already had them), False if it could not be fixed."""
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
    except OSError as exc:
        if not warn:
            return False
        pgroup = pgroup_of_path(path)
        if acl_grants_group_write(path, pgroup):
            return True  # already handled by an ACL, mode bits are irrelevant
        kind = "directory" if stat.S_ISDIR(st.st_mode) else "file"
        fix = "chmod g+rwXs" if stat.S_ISDIR(st.st_mode) else "chmod g+rw"
        warn_once(
            ("mode", str(path)),
            f"{kind} {path} is not group-writable (mode "
            f"{stat.S_IMODE(st.st_mode):04o}, owner "
            f"{_owner_name(st)}) and eco could not change it ({exc.strerror}). "
            f"Other members of {pgroup or 'the group'} will not be able to "
            f"write there -- fix with: {fix} '{path}'",
        )
        return False


def _owner_name(st):
    try:
        import pwd

        return pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, OSError, ImportError):
        return str(st.st_uid)


def _ensure_group(path, pgroup=None, warn=True):
    """Make `path` belong to its pgroup. True if it does (or the path is not
    inside a pgroup tree, where eco has no business picking a group)."""
    pgroup = pgroup or pgroup_of_path(path)
    if pgroup is None:
        return True
    gid = _gid_of(pgroup)
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
    except OSError as exc:
        if warn:
            warn_once(
                ("group", str(path)),
                f"{path} belongs to group {_name_of_gid(st.st_gid)} instead of "
                f"{pgroup} and eco could not change it ({exc.strerror}) -- "
                f"members of {pgroup} may not be able to write it. Fix with: "
                f"chgrp {pgroup} '{path}'",
            )
        return False


def ensure_group_writable(path, pgroup=None, warn=True):
    """Make one existing file or directory group-writable and pgroup-owned.

    Returns True if `path` ends up both, False if something could not be fixed
    (already reported via `warn_once` unless ``warn=False``).
    """
    path = Path(path)
    try:
        is_dir = path.is_dir()
    except OSError:
        return False
    ok_group = _ensure_group(path, pgroup=pgroup, warn=warn)
    ok_mode = _ensure_bits(path, _DIR_BITS if is_dir else _FILE_BITS, warn=warn)
    return ok_group and ok_mode


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
    """`open()` for a results file, leaving it group-writable and pgroup-owned.

    The parent directory is created (via `ensure_dir`) if missing, and the
    permissions are applied on the *open descriptor*, so they land on the file
    this call actually wrote even if it is renamed afterwards.

    An existing file that is already group-writable is left alone -- notably it
    is not an error to be unable to chmod a file another account created, as
    long as the group can write it, which is the whole point.
    """
    path = Path(path)
    if not path.parent.exists():
        ensure_dir(path.parent, pgroup=pgroup, warn=warn)

    with open(path, mode, **kwargs) as fh:
        try:
            yield fh
        finally:
            _fix_open_file(fh, path, pgroup=pgroup, warn=warn)


def _fix_open_file(fh, path, pgroup=None, warn=True):
    """Apply the file mode/group to an open descriptor, best effort."""
    try:
        fd = fh.fileno()
        st = os.fstat(fd)
    except (OSError, ValueError, AttributeError):
        return

    gid = _gid_of(pgroup or pgroup_of_path(path) or "")
    if gid is not None and st.st_gid != gid:
        try:
            os.fchown(fd, -1, gid)
        except OSError:
            _ensure_group(path, pgroup=pgroup, warn=warn)

    if st.st_mode & _FILE_BITS != _FILE_BITS:
        try:
            os.fchmod(fd, stat.S_IMODE(st.st_mode) | _FILE_BITS)
        except OSError:
            _ensure_bits(path, _FILE_BITS, warn=warn)


def check_group_writable(path, warn=True):
    """Report (without changing anything) whether `path` and its ancestors up
    to the pgroup root are writable by the pgroup.

    Meant for interactive use -- ``eco.utilities.datafiles.check_group_writable(
    "/sf/bernina/data/p23415/res/run_data")`` -- when a collaborator says they
    cannot write somewhere. Returns the list of offending paths, empty if all
    is well.
    """
    path = Path(path)
    pgroup = pgroup_of_path(path)
    bad = []
    for level in _levels_below_pgroup_root(path):
        try:
            st = os.stat(level)
        except OSError:
            continue
        bits = _DIR_BITS if stat.S_ISDIR(st.st_mode) else _FILE_BITS
        if st.st_mode & bits == bits and (
            pgroup is None or st.st_gid == _gid_of(pgroup)
        ):
            continue
        if acl_grants_group_write(level, pgroup):
            continue
        bad.append(level)
        if warn:
            print(
                f"  {stat.filemode(st.st_mode)} {_owner_name(st):>14s} "
                f"{_name_of_gid(st.st_gid):>14s}  {level}"
            )
    return bad
