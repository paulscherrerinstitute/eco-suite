"""Load the exchangeable XRD/GPS modules (Kappa, SampleStage) onto their
PowerBrick, from eco.

The Kappa and the SampleStage are run by one dedicated PowerBrick per
diffractometer (``SARES21-CPPM-XRD1``, ``SARES22-CPPM-GPS1``), which boots
without a motor configuration on purpose (a wrong one can destroy motors).
Loading one copies its ``add_device*.cmd`` to the PowerBrick and restarts the
IOC. This is done by the controls group's ``ConfigMotorIOC.py``; the methods
here run that script rather than a copy of it, so the hosts, configs and the
open-loop table stay the ones the controls group maintains. eco adds:

* a confirmation (the script's safety password ``deltatau``),
* eco's write gates (access control, queue claims) for the module's motors,
* a refusal while any of the module's motors is moving,
* a check over channel access afterwards whether the motor records came up.

>>> xrd.module_ioc                     # host, which config is loaded
>>> xrd.module_ioc.load("kappa")       # asks, copies, restarts the IOC (~30 s)
>>> xrd.module_ioc.list_motors()       # motor records of the running IOC
>>> xrd.module_ioc.open_loop(xrd.eta_kap, xrd.kappa)  # without encoder

The script is started with eco's own interpreter (it only needs paramiko), not
through the ``/sf/controls/bin/ConfigMotorIOC`` wrapper, which mails the
controls group whenever it judges the python environment unfit.
"""

import os
import subprocess
import sys
import time

SCRIPT = "/sf/controls/applications/ConfigMotorIOC/ConfigMotorIOC.py"
SAFETY_PASSWORD = "deltatau"

# diffractometer PV prefix -> short host name known to ConfigMotorIOC.py
HOSTS = {"SARES21-XRD": "XRD", "SARES22-GPS": "GPS"}
FULL_HOSTS = {"XRD": "SARES21-CPPM-XRD1", "GPS": "SARES22-CPPM-GPS1"}

# config name -> motor records its add_device*.cmd creates
# (ESB_GPS_XRD db/Kappa.subs, db/SampleStage.subs)
CONFIG_MOTORS = {
    "kappa": ["MOT_KAP_KRX", "MOT_KAP_KAP", "MOT_KAP_KPH", "MOT_KAP_DTX",
              "MOT_KAP_DTZ", "MOT_KAP_DTY", "MOT_KAP_DRX", "MOT_KAP_DRZ"],
    "stage": ["MOT_TBL_TX", "MOT_TBL_TZ", "MOT_TBL_TY", "MOT_TBL_RX",
              "MOT_TBL_RZ", "MOT_HEX_RX", "MOT_HEX_TX"],
}


class ModuleIoc:
    """The module PowerBrick of one diffractometer (`gps` or `xrd`).

    Plain object, not part of status: nothing here runs unless called."""

    def __init__(self, diffractometer):
        self._diffractometer = diffractometer
        self.pvname = diffractometer.pvname
        self.host = HOSTS[self.pvname]

    def __repr__(self):
        try:
            loaded = self.loaded()
        except Exception as e:
            loaded = f"unknown ({e})"
        return (
            f"Module PowerBrick {FULL_HOSTS[self.host]} of {self.pvname}: "
            f"loaded config: {loaded}\n"
            f"configs: {', '.join(CONFIG_MOTORS)}; methods: loaded(), "
            "load(config), list_motors(), open_loop(*motors)"
        )

    # --- reading -----------------------------------------------------------
    def loaded(self, timeout=1.0):
        """Name of the config whose motor records are on the network, or None."""
        for config, motors in CONFIG_MOTORS.items():
            if _ca_get(f"{self.pvname}:{motors[0]}.NAME", timeout) is not None:
                return config
        return None

    def list_motors(self):
        """Print the motor records of the running IOC (``dbl motor``)."""
        self._run_script(["--host", self.host, "-m", "0x2"])

    # --- changing ----------------------------------------------------------
    def load(self, config, force=False, timeout=60):
        """Copy `config` ("kappa" or "stage") to the PowerBrick, restart its
        IOC and wait until the config's motor records are connected.

        All motors on this PowerBrick stop being controlled while the IOC
        restarts. A wrong config can destroy motors: you are asked for the
        safety password unless `force=True`."""
        if config not in CONFIG_MOTORS:
            raise ValueError(f"config must be one of {list(CONFIG_MOTORS)}")
        current = self.loaded()
        self._check_motors(self._module_motors(), "restart the IOC under")
        self._confirm(
            f"Load {config!r} onto {FULL_HOSTS[self.host]} (currently: {current}) "
            "and restart its IOC.",
            force,
        )
        self._run_script(["--host", self.host, "--cfg", config, "-m", "0x2"])
        self._wait_for_records(config, timeout)

    def open_loop(self, *motors, force=False):
        """Switch the given kappa motors to open loop without encoder.

        Takes motor objects (``xrd.eta_kap``) or PV names. Only motors that
        ConfigMotorIOC.py has an open-loop setting for are accepted. Takes
        effect on the PowerBrick immediately, without IOC restart; loading the
        config again (``load("kappa")``) brings the encoders back."""
        if not motors:
            raise ValueError("name at least one motor")
        pvnames = [getattr(m, "pvname", m) for m in motors]
        with open(SCRIPT) as f:
            source = f.read()
        unknown = [p for p in pvnames if f"'{p}'" not in source]
        if unknown:
            raise ValueError(f"{SCRIPT} has no open-loop setting for {unknown}")
        if self.loaded() != "kappa":
            raise RuntimeError(f"kappa is not loaded on {FULL_HOSTS[self.host]}")
        self._check_motors(
            [m for m in self._module_motors() if m.pvname in pvnames],
            "switch to open loop",
        )
        self._confirm(
            f"Switch {', '.join(pvnames)} on {FULL_HOSTS[self.host]} to open loop "
            "without encoder.",
            force,
        )
        self._run_script(["--host", self.host, "--noenc", ",".join(pvnames), "-m", "0x0"])

    # --- helpers -----------------------------------------------------------
    def _module_motors(self):
        """The diffractometer's motor objects that live on this PowerBrick."""
        prefixes = tuple(
            f"{self.pvname}:{m}" for motors in CONFIG_MOTORS.values() for m in motors
        )
        return [
            obj
            for obj in vars(self._diffractometer).values()
            if getattr(obj, "pvname", None) in prefixes
        ]

    def _check_motors(self, motors, action):
        from eco.devices_general.utilities import _access_gate, _claim_gate

        for m in motors:
            _access_gate(m)
            _claim_gate(m)
        moving = [
            m.pvname
            for m in motors
            if _ca_get(m.pvname + ".DMOV", 0.5) == 0
        ]
        if moving:
            raise RuntimeError(f"not going to {action} moving motors: {moving}")

    def _confirm(self, what, force):
        print(what)
        print("A wrong PowerBrick configuration can destroy motors.")
        if force:
            return
        answer = input(f"type the safety password ({SAFETY_PASSWORD}) to go on: ")
        if answer.strip() != SAFETY_PASSWORD:
            raise RuntimeError("not confirmed, nothing done")

    def _run_script(self, args):
        """Run ConfigMotorIOC.py, passing its output through as it comes."""
        if not os.path.exists(SCRIPT):
            raise FileNotFoundError(f"{SCRIPT} (only on the beamline consoles)")
        proc = subprocess.Popen(
            [sys.executable, SCRIPT, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            # read1: the script prints '*' progress without newlines
            while chunk := proc.stdout.read1(4096):
                sys.stdout.write(chunk.decode(errors="replace"))
                sys.stdout.flush()
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
        # the script exits 0 even when it gave up, only a traceback is non-zero
        if proc.returncode:
            raise RuntimeError(f"ConfigMotorIOC.py failed (exit {proc.returncode})")

    def _wait_for_records(self, config, timeout):
        names = [f"{self.pvname}:{m}" for m in CONFIG_MOTORS[config]]
        t0 = time.time()
        while True:
            missing = [n for n in names if _ca_get(n + ".NAME", 1.0) is None]
            if not missing:
                print(f"{config}: all {len(names)} motor records connected")
                return
            if time.time() - t0 > timeout:
                raise TimeoutError(f"{config} loaded, but not connected: {missing}")
            time.sleep(2)


def _ca_get(pvname, timeout):
    """Value of `pvname`, or None if it does not connect (epics.caget prints
    "cannot connect to ..." for every absent record, this does not)."""
    from epics import PV

    pv = PV(pvname, auto_monitor=False)
    if not pv.wait_for_connection(timeout=timeout):
        return None
    return pv.get(timeout=timeout)
