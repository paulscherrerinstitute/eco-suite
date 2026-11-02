import atexit
import getpass
import os
import re
import subprocess
import threading
import time

from eco import Assembly
from eco.elements.assembly import StatusCollection
from eco.elements.detector import DetectorGet
from eco.elements.adjustable import AdjustableGetSet
from .PBComm import SSHComm
from .powerbrick_parameters import *

connected_power_bricks = {}
_connected_power_bricks_lock = threading.Lock()

# controllers not reachable from the consoles (machine network, e.g. the KB
# mirrors' SAROP21-CPPM-*) are reached through this host -- only after
# someone logged into it with their own account in this session (unlock())
JUMP_HOST = "sf-gw"
# accounts several people use: unlock() asks whose login to use
SHARED_ACCOUNTS = ("gac-bernina",)

_jump_socket = None  # ControlPath of this session's login to JUMP_HOST
_jump_user = None


def gateway_open():
    """Is this session's login to JUMP_HOST (see unlock()) alive?"""
    if _jump_socket is None:
        return False
    check = subprocess.run(
        ["ssh", "-S", _jump_socket, "-O", "check", JUMP_HOST],
        capture_output=True,
    )
    return check.returncode == 0


def unlock(user=None, persist="1h"):
    """Open the PowerBrick parameters behind JUMP_HOST for this session.

    Logs into JUMP_HOST as `user` -- your own PSI account; ssh asks for the
    password itself (eco never sees it), with a Kerberos ticket it does not
    ask at all. The login stays open as a private master connection that
    the PowerBrick sessions are tunnelled through; it closes with lock(),
    when the session ends, or `persist` after its last use."""
    global _jump_socket, _jump_user
    if gateway_open():
        print(f"\U0001F513 already open ({_jump_user}@{JUMP_HOST}); lock() closes it")
        return
    if user is None:
        user = getpass.getuser()
        if user in SHARED_ACCOUNTS:
            user = input(f"your PSI account for {JUMP_HOST}: ").strip()
            if not user:
                raise RuntimeError("no account given, nothing opened")
    run_dir = os.path.expanduser("~/.cache/eco")
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    os.chmod(run_dir, 0o700)
    sock = os.path.join(run_dir, f"pb-gateway-{os.getpid()}.sock")
    login = subprocess.run(
        [
            "ssh", "-fN",
            "-o", "ControlMaster=yes",
            "-o", f"ControlPath={sock}",
            "-o", f"ControlPersist={persist}",
            "-o", "ConnectTimeout=10",
            f"{user}@{JUMP_HOST}",
        ]
    )
    if login.returncode != 0:
        raise PermissionError(f"login to {JUMP_HOST} as {user} failed")
    _jump_socket, _jump_user = sock, user
    print(
        f"\U0001F513 PowerBrick parameters behind {JUMP_HOST} open for this "
        f"session ({user}@{JUMP_HOST}); powerbrick.lock() closes them"
    )


def lock():
    """Close this session's login to JUMP_HOST and the sessions through it."""
    global _jump_socket, _jump_user
    for comm in list(connected_power_bricks.values()):
        if comm.jump:
            comm.disconnect()
    if _jump_socket is not None:
        subprocess.run(
            ["ssh", "-S", _jump_socket, "-O", "exit", JUMP_HOST],
            capture_output=True,
        )
    _jump_socket = _jump_user = None


atexit.register(lock)


def get_power_brick_comm(hostname):
    # one PowerBrickComm (and so at most one SSH session) per controller, also
    # when many motors ask for it at once during a parallel init
    with _connected_power_bricks_lock:
        if hostname not in connected_power_bricks:
            connected_power_bricks[hostname] = PowerBrickComm(hostname)
        return connected_power_bricks[hostname]


class PowerBrickComm:
    """gpascii on a PowerBrick, over one SSH session opened on first use.

    Every request/reply pair holds `_lock`: all motors of a controller share
    the one channel, and get_status() reads them from 20 threads at once --
    without the lock the threads read each other's replies (measured on
    SARES21-CPPM-XRD4: 48 of 48 parallel reads failed).

    Parameters registered via `register()` are read together in one write
    (`refresh()`), and reads with `max_age` are answered from that snapshot
    while it is younger than `max_age` seconds. That turns a status read of a
    whole diffractometer (~1400 parameters) into a few batches instead of one
    round trip per parameter."""

    batch_size = 200

    def __init__(self, hostname):
        self.hostname = hostname
        self.pbsshcom = None
        self.jump = None
        self._lock = threading.RLock()
        self._registered = {}  # parstring -> None, ordered set
        self._snapshot = {}  # parstring -> reply string or Exception
        self._snapshot_time = 0.0

    @property
    def connected(self):
        return self.pbsshcom is not None

    def connect(self):
        with self._lock:
            if self.pbsshcom is None:
                com = SSHComm()
                jump = None if _reachable(self.hostname) else JUMP_HOST
                if jump and not gateway_open():
                    lock()  # forget a login that has timed out
                    raise PermissionError(_LOCKED_MESSAGE)
                com.connect(self.hostname, jump=jump, jump_socket=_jump_socket)
                self.jump = jump
                self.pbsshcom = com
            return self.pbsshcom

    def disconnect(self):
        with self._lock:
            com, self.pbsshcom = self.pbsshcom, None
            self._snapshot = {}
            if com is not None:
                try:
                    com.client.close()
                except Exception:
                    pass

    def register(self, parstrings):
        with self._lock:
            for p in parstrings:
                self._registered[_strip(p)] = None

    def _request(self, lines):
        """Send `lines` in one write, return one reply string per line."""
        com = self.connect()
        try:
            com.drain()
            com.chan.send("".join(l + "\n" for l in lines))
            return com.read_acks(len(lines))
        except (OSError, EOFError):  # TimeoutError included
            # dropped session, or replies may still be in flight and would
            # be taken for the next request's: the next request reconnects
            self.disconnect()
            raise

    def refresh(self):
        """Read all registered parameters in batches into the snapshot."""
        with self._lock:
            pars = list(self._registered)
            snapshot = {}
            for i in range(0, len(pars), self.batch_size):
                chunk = pars[i : i + self.batch_size]
                for p, reply in zip(chunk, self._request(chunk)):
                    snapshot[p] = reply
            self._snapshot = snapshot
            self._snapshot_time = time.time()

    def get_parameter(self, parstring, autoconvert=True, max_age=None):
        if isinstance(parstring, bytes):
            parstring = parstring.decode()
        parstring = _strip(parstring)
        with self._lock:
            if max_age is not None and parstring in self._registered:
                if (
                    time.time() - self._snapshot_time > max_age
                    or parstring not in self._snapshot
                ):
                    self.refresh()
                par = self._snapshot[parstring]
            else:
                (par,) = self._request([parstring])
        if "error #" in par:
            raise ValueError(f"{self.hostname}: {par.strip()}")
        if autoconvert:
            par = _convert(par, parstring)
        return par

    def set_parameter(self, value, parstring):
        if isinstance(parstring, bytes):
            parstring = parstring.decode()
        setstring = _strip(parstring) + "=" + str(value)
        with self._lock:
            self.connect()
            self._snapshot_time = 0.0
            if self.pbsshcom.iawrite(setstring):
                return 1  # handled locally (define/open/close), nothing sent
            # consume the ACK, otherwise the next read gets it as its reply
            (reply,) = self.pbsshcom.read_acks(1)
        if "error #" in reply:
            raise ValueError(f"{self.hostname}: {reply.strip()}")

    # def set_hmz


_LOCKED_MESSAGE = (
    f"\U0001F511 behind {JUMP_HOST}, locked -- powerbrick.unlock() (or "
    "<motor>.cnf_pb.unlock()) to log in with your own account"
)


def _behind_gateway(hostname):
    """Is the motor record served through a CA gateway rather than by the
    PowerBrick itself? Decided from the CA server name, which pyepics already
    has -- no network traffic. Unknown counts as not behind."""
    if not callable(hostname):
        return False
    try:
        host = hostname()
    except Exception:
        return False
    return bool(host) and "-CPPM-" not in host.upper()


class _GatedStatusCollection(StatusCollection):
    """Lists nothing while `locked()` is true, so status/repr of a locked
    PowerBrickChannelPars never reaches for the controller."""

    def __init__(self, parent, locked, name="status_collection"):
        super().__init__(parent, name=name)
        self._locked = locked

    def get_list(self, selection=None, **kwargs):
        if self._locked():
            return []
        return super().get_list(selection=selection, **kwargs)


def _reachable(hostname, port=22, timeout=2.0):
    """Can the controller's SSH port be reached directly from here?"""
    import socket

    try:
        socket.create_connection((hostname, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _strip(parstring):
    return parstring.strip("\n").strip()


def _convert(par, parstring):
    par = par.lower().split(parstring.lower())[1]
    if par.startswith("="):
        par = par[1:]
    try:
        return int(par)
    except ValueError:
        try:
            return float(par)
        except ValueError:
            return par


class PowerBrickChannelPars(Assembly):
    """gpascii parameters of one PowerBrick axis.

    Building it does not contact anything; the first read (normally the
    first get_status() / status()) finds controller and axis and opens the
    SSH session.

    `hostname` may be a callable, `pvname` is the motor record. With a
    `pvname`, the axis is taken from the record's OUT link ("@asyn(PPMAC1,6)"),
    i.e. the axis the IOC really drives, and `axis` is only the fallback for
    records whose OUT says nothing; the controller is the motor record's IOC
    host, looked up in iocinfo.psi.ch when channel access only shows a
    gateway (see `ioc_host`).

    Controllers behind the CA gateway (KB mirrors, ...) sit on a network the
    consoles reach only through JUMP_HOST. Their parameters are for
    development: shown as \U0001F511 locked, not read by status, until someone
    logs into JUMP_HOST with their own account -- `unlock()`."""

    # status reads of all axes of a controller share one batched read
    snapshot_max_age = 1.0
    # after a failed lookup of controller/axis (record offline), reads fail
    # at once for this many seconds before it is tried again
    retry_after = 30.0

    def __init__(
        self, hostname=None, axis=None, type=None, pars_list=[], pvname=None, name=None
    ):
        super().__init__(name=name)
        self._behind_gateway = _behind_gateway(hostname)
        if self._behind_gateway:
            # before any _append: the children go into the gated collection
            self.status_collection = _GatedStatusCollection(
                self, locked=lambda: _jump_socket is None
            )
            # a value for the motor's display / status: the key
            self.get_current_value = self._lock_state
        self._hostname = hostname
        self._pvname = pvname
        self._axis_fallback = axis
        self._ch = None
        self._pbcom = None
        self._resolve_lock = threading.Lock()
        self._resolve_failed = (0.0, None)  # (time, exception)

        if type == "motor":
            pars_list = pars_motors

        else:
            pass

        self._read_pars = [par for par, mode in pars_list if "r" in mode]
        for par, mode in pars_list:
            if "r" in mode and "w" not in mode:
                self.append_par_detector(par)
            elif "r" in mode and "w" in mode:
                # print(par)
                self.append_par_adjustable(par)

    def _resolve(self):
        """Controller and axis, found once on first use."""
        with self._resolve_lock:
            if self._pbcom is None:
                # an offline record: fail fast for a while instead of every
                # parameter read waiting for .OUT again
                t_failed, failure = self._resolve_failed
                if failure is not None and time.time() - t_failed < self.retry_after:
                    raise failure
                try:
                    self._resolve_once()
                except Exception as e:
                    self._resolve_failed = (time.time(), e)
                    raise
        return self._pbcom

    def _resolve_once(self):
        """Find controller and axis; raises if the record is offline."""
        axis = axis_from_out(self._pvname) if self._pvname else None
        self.axis_source = "OUT" if axis is not None else "fallback"
        if axis is None:
            axis = self._axis_fallback
        if axis is None:
            raise ValueError(f"{self._pvname}: no PowerBrick axis known")
        host = self._hostname
        if callable(host):
            # the CA server: the PowerBrick itself, unless the record
            # is reached through a gateway
            try:
                host = host()
            except Exception:
                host = None
            if host is None or "-CPPM-" not in host.upper():
                host = None
        if host is None:
            host = ioc_host(self._pvname)
        self._ch = axis
        comm = get_power_brick_comm(host)
        comm.register(par.format(axis) for par in self._read_pars)
        self._pbcom = comm

    @property
    def locked(self):
        return self._behind_gateway and _jump_socket is None

    def _lock_state(self):
        if self.locked:
            return f"\U0001F511 locked ({JUMP_HOST}), .unlock()"
        return f"\U0001F513 open via {_jump_user}@{JUMP_HOST}"

    def __repr__(self):
        if self.locked:
            return f"{self.alias.get_full_name()}: {_LOCKED_MESSAGE}"
        return super().__repr__()

    def _check_lock(self):
        if self.locked:
            raise PermissionError(_LOCKED_MESSAGE)

    def unlock(self, user=None):
        """Log into JUMP_HOST with your own account (see powerbrick.unlock)."""
        unlock(user=user)

    def lock(self):
        lock()

    @property
    def axis(self):
        self._check_lock()
        self._resolve()
        return self._ch

    @property
    def host(self):
        self._check_lock()
        return self._resolve().hostname

    def get_from_par(self, formstr, **kwargs):
        kwargs.setdefault("max_age", self.snapshot_max_age)
        self._check_lock()
        com = self._resolve()
        return com.get_parameter(formstr.format(self._ch), **kwargs)

    def set_from_par(self, value, formstr, **kwargs):
        self._check_lock()
        com = self._resolve()
        return com.set_parameter(value, formstr.format(self._ch), **kwargs)

    def append_par_detector(self, par):
        self._append(DetectorGet, lambda: self.get_from_par(par), name=str2varname(par))

    def append_par_adjustable(self, par):
        self._append(
            AdjustableGetSet,
            lambda: self.get_from_par(par),
            lambda value: self.set_from_par(value, par),
            name=str2varname(par),
        )


_OUT_AXIS = re.compile(r"@asyn\(\s*[^,\s]+\s*,\s*(\d+)")


def axis_from_out(pvname, timeout=1.0):
    """Axis number in a motor record's OUT link ("@asyn(PPMAC1,6)" -> 6), or
    None if the record does not answer or OUT has no asyn address."""
    from epics import PV

    pv = PV(pvname + ".OUT", auto_monitor=False)
    if not pv.wait_for_connection(timeout=timeout):
        return None
    out = pv.get(as_string=True, timeout=timeout)
    m = _OUT_AXIS.match(out or "")
    return int(m.group(1)) if m else None


_PPMAC_OUT = re.compile(r"@asyn\(\s*PPMAC\d*\s*,")


def is_powerbrick_motor(motor, timeout=1.0):
    """Is `motor` (a MotorRecord) driven by a PowerBrick?

    Served by a PowerBrick IOC (CA server "*-CPPM-*") -- known locally, no
    traffic; behind a CA gateway, its OUT link names the PowerBrick asyn port
    ("@asyn(PPMAC1,6)"; MForce and others use other port names)."""
    try:
        if "-CPPM-" in motor.host.get_current_value().upper():
            return True
    except Exception:
        pass
    from epics import PV

    pv = PV(motor.pvname + ".OUT", auto_monitor=False)
    if not pv.wait_for_connection(timeout=timeout):
        return False
    return bool(_PPMAC_OUT.match(pv.get(as_string=True, timeout=timeout) or ""))


def ioc_host(pvname):
    """Host of the IOC serving `pvname`, from iocinfo.psi.ch.

    Needed where channel access only shows the CA gateway (e.g. the KB
    mirrors, sf-saresb-cagw-01); a PowerBrick runs its own IOC, so this is
    the controller."""
    from eco.epics_utils import iocinfo

    iocs = {r.ioc for r in iocinfo.search_records(pvname, fuzzy=False)}
    if len(iocs) != 1:
        raise LookupError(f"{pvname}: IOC not found in iocinfo ({sorted(iocs)})")
    (ioc,) = iocs
    return iocinfo.get_ioc(ioc).hostname or ioc


def str2varname(s, delimeter_swaps=[(".", "_")]):
    for tds in delimeter_swaps:
        s = s.replace(*tds)
    s = "".join(filter(lambda c: str.isidentifier(c) or str.isdecimal(c), s))
    return s
