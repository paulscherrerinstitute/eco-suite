import threading
import time

from eco import Assembly
from eco.elements.detector import DetectorGet
from eco.elements.adjustable import AdjustableGetSet
from .PBComm import SSHComm
from .powerbrick_parameters import *

connected_power_bricks = {}
_connected_power_bricks_lock = threading.Lock()


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
                com.connect(self.hostname)
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

    Building it does not contact the controller; the SSH session is opened
    by the first read, normally the first get_status() / status()."""

    # status reads of all axes of a controller share one batched read
    snapshot_max_age = 1.0

    def __init__(self, hostname, axis=None, type=None, pars_list=[], name=None):
        super().__init__(name=name)
        self._pbcom = get_power_brick_comm(hostname)
        self._ch = axis

        if type == "motor":
            pars_list = pars_motors

        else:
            pass

        self._pbcom.register(
            par.format(self._ch) for par, mode in pars_list if "r" in mode
        )
        for par, mode in pars_list:
            if "r" in mode and "w" not in mode:
                self.append_par_detector(par)
            elif "r" in mode and "w" in mode:
                # print(par)
                self.append_par_adjustable(par)

    def get_from_par(self, formstr, **kwargs):
        kwargs.setdefault("max_age", self.snapshot_max_age)
        return self._pbcom.get_parameter(formstr.format(self._ch), **kwargs)

    def set_from_par(self, value, formstr, **kwargs):
        return self._pbcom.set_parameter(value, formstr.format(self._ch), **kwargs)

    def append_par_detector(self, par):
        self._append(DetectorGet, lambda: self.get_from_par(par), name=str2varname(par))

    def append_par_adjustable(self, par):
        self._append(
            AdjustableGetSet,
            lambda: self.get_from_par(par),
            lambda value: self.set_from_par(value, par),
            name=str2varname(par),
        )


def str2varname(s, delimeter_swaps=[(".", "_")]):
    for tds in delimeter_swaps:
        s = s.replace(*tds)
    s = "".join(filter(lambda c: str.isidentifier(c) or str.isdecimal(c), s))
    return s
