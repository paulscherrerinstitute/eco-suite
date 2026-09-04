"""The physical manual-control box as an ordinary eco component.

Everything you need to run, diagnose and repair the box lives on the object
`bernina.manual_control_box` - including the full manual, in this class's
docstring (`manual_control_box.manual()` prints it).
"""

import os
import subprocess

from ..elements.assembly import Assembly, iter_ancestor_assemblies

DEFAULT_HOST = "ecobox"
DEFAULT_USER = "gac-bernina"
DEFAULT_PORT = 8791
DEFAULT_TOKEN_FILE = "~/.eco/pendant_token"
BOX_INSTALL_DIR = "/opt/eco-control-box"
BOX_SERVICE = "eco-control-box"


class ControlBox(Assembly):
    """Handheld manual-control box (Raspberry Pi pendant) - full manual.

    ================================================================
    WHAT IT IS
    ================================================================
    A 7" touchscreen box with a joystick and a rotary encoder that drives
    any eco Adjustable: turn the knob to walk the device tree, press to arm
    what you land on, push the stick to jog it. The hardware is the PSI
    SwissFEL "Motor Control Unit" box built in 2018 (Raspberry Pi 3B, 7"
    800x480 touchscreen, MCP3008 joystick ADC, KY-040 encoder, powered and
    networked over one PoE cable). Only the hardware is reused - the box
    runs eco's own thin client, not the 2018 PyQt/EPICS software.

    Split of work: the box holds NO eco. It is a thin client that sends
    small events and renders what it is sent. All eco/EPICS logic stays in
    your session on the console, which is why the box drives the very same
    objects your shell does.

    ================================================================
    QUICK START
    ================================================================
        bernina.manual_control_box.start()      # serve this namespace
        bernina.manual_control_box.status()     # is it connected?
        bernina.manual_control_box.stop()

    The box connects out to the console and retries forever, so power-up
    order does not matter and it reconnects on its own after a restart.
    Only ONE session can serve it (one TCP port); if another already does,
    `.competing()` says which process.

    ================================================================
    LOGGING IN TO THE BOX
    ================================================================
        ssh gac-bernina@ecobox

    If the host key was replaced (the SD card was reflashed):
        ssh-keygen -R ecobox         # then ssh again and accept the new key

    From here, `.ssh("<command>")` runs a command on the box and returns its
    output, and `.logs()`, `.service()`, `.probe()` wrap the common ones.

    ================================================================
    WHAT LIVES WHERE ON THE BOX
    ================================================================
        /opt/eco-control-box/manual_control/   the thin client (no eco)
        /etc/eco-control-box.env               PC_HOST / PC_PORT it calls
        /etc/eco-control-box.token             shared token (permanent)
        systemd unit: eco-control-box          fullscreen app, starts at boot

    On the console side the same token must be in ~/.eco/pendant_token. It
    never expires and survives reboots and re-installs; it changes only if
    you delete the file or reflash the card. If the two ever differ, write
    any string to both and restart the server.

    ================================================================
    "THE BOX SHOWS THE WAITING SCREEN"
    ================================================================
    The box shows a dark waiting screen reading "waiting for the eco
    session", the host:port it is calling, a retry counter, and its own
    hostname/IP at the bottom. That screen means the box itself is healthy
    and nothing is serving it - it retries every 3 s and switches to the
    control view by itself the moment a server appears. Check, in order:

    1. Is a server running here?        .status()  /  .competing()
    2. Is another session holding it?   .competing() names PID and user;
                                        stop it there with box.stop(), or
                                        kill that PID.
    3. Is the box calling the right host?
                                        .box_config()  -> PC_HOST/PC_PORT
                                        (edit /etc/eco-control-box.env, or
                                        re-run setup_box.sh with the right
                                        host, then restart the service)
    4. Token mismatch shows on the server as "rejected client: bad or
       missing token".

    ================================================================
    "TOUCH WORKS BUT THE KNOB AND STICK DO NOTHING"
    ================================================================
    The encoder (GPIO) and the joystick (SPI/MCP3008) are independent and
    fail independently; the startup line in the journal says which:

        .logs(grep="hardware input")

    Common causes:
      * "Unable to load any default pin factory!" - gpiozero has no
        backend: sudo apt install python3-lgpio  (encoder dead)
      * no /dev/spidev* - SPI not enabled:
        sudo raspi-config nonint do_spi 0 && sudo reboot   (joystick dead)
      * user not in the gpio/spi groups - the service cannot open the
        devices: sudo usermod -aG gpio,spi,i2c gac-bernina, then reboot

    Then watch the raw hardware live, on the box:

        cd /opt/eco-control-box
        python3 -m manual_control.remote.pi_hardware --probe

    Centre reads about 0.5, ends 0.0 and 1.0. If X and Y move together the
    two analog lines are shorted - a fault the 2018 build report already
    warned about for this box.

    ================================================================
    WIRING (preset "psi-mcu-box")
    ================================================================
        encoder A / B / push      GPIO 5 / 6 / 13
        extra button (disarm)     GPIO 26
        joystick X / Y            MCP3008 CH2 / CH1
        joystick push switch      MCP3008 CH0   (not a GPIO)
        MCP3008                   SPI0
        7" touchscreen            DSI + I2C touch, leaves SPI0 free

    Corrections if an axis runs backwards or the button is inverted live in
    manual_control/remote/pi_hardware.py: invert_x, invert_y,
    joy_sw_active_low.

    ================================================================
    USING IT
    ================================================================
    Knob turns move the cursor, press enters a branch or arms a leaf, long
    press disarms. Pressing the knob also cycles mode: NAVIGATE (turn =
    browse) and STEP (turn = step size). Stick up/down jogs the armed
    target; left/right navigates. On the touchscreen, tap a row to
    enter/arm, tap a breadcrumb to jump back up, and use the -/+ and
    Disarm buttons in the right-hand column.

    Motors with a native jog (MotorRecord, via JOGF/JOGR) are jogged by the
    IOC itself; everything else is stepped repeatedly by the console, so
    the link never makes jogging stutter.

    ================================================================
    UPDATING THE SOFTWARE ON THE BOX
    ================================================================
    Build a fresh bundle here, copy it over, re-run the installer:

        eco/manual_control/hardware/make_sdcard_payload.sh ~/eco_box_payload <this-host> 8791
        scp ~/eco_box_payload/eco_control_box.tar.gz gac-bernina@ecobox:/tmp/
        ssh gac-bernina@ecobox
          sudo tar -xzf /tmp/eco_control_box.tar.gz -C /opt/eco-control-box
          sudo /opt/eco-control-box/manual_control/remote/pi_setup/setup_box.sh <this-host> 8791
          sudo systemctl restart eco-control-box

    Changing what the box *shows* (components, values, tree) needs no box
    update at all - the console streams it. Only GUI/widget changes do.
    """

    def __init__(self, name=None, namespace=None, host=DEFAULT_HOST, user=DEFAULT_USER,
                 port=DEFAULT_PORT, token_file=DEFAULT_TOKEN_FILE, bind="0.0.0.0"):
        super().__init__(name=name)
        self.host = host
        self.user = user
        self.port = port
        self.bind = bind
        self.token_file = token_file
        self._namespace = namespace
        self.server = None

    # --- the manual ---------------------------------------------------
    def manual(self):
        """Print the full manual (this class's docstring)."""
        print(self.__class__.__doc__)

    # --- serving ------------------------------------------------------
    def _resolve_namespace(self):
        """The namespace to serve: an explicit one, else the assembly this
        component was appended into (normally the instrument namespace)."""
        if self._namespace is not None:
            return self._namespace
        for ancestor in iter_ancestor_assemblies(self):
            return ancestor  # nearest first; for us that is the namespace
        raise RuntimeError(
            "no namespace to serve: this component was not appended into one. "
            "Pass it explicitly, e.g. manual_control_box.start(bernina.namespace)"
        )

    def start(self, namespace=None, port=None, bind=None, token=None, **box_kwargs):
        """Serve a namespace to the box, in the background. Returns the server."""
        from .remote.serve import start_box_server

        if self.is_serving:
            print(f"already serving: {self.server!r}")
            return self.server
        ns = namespace if namespace is not None else self._resolve_namespace()
        self.server = start_box_server(
            ns, root_name=getattr(ns, "name", None), port=port or self.port,
            bind=bind or self.bind, token=token, token_file=self.token_file,
            **box_kwargs,
        )
        return self.server

    def stop(self):
        """Stop this session's server (the box falls back to its waiting screen)."""
        if self.server is None:
            print("no server running in this session")
            return
        self.server.stop()
        self.server = None
        print("control box server stopped")

    @property
    def is_serving(self):
        return self.server is not None and not self.server._stop.is_set()

    @property
    def is_connected(self):
        return bool(self.server is not None and self.server.connected)

    # --- who else is serving -------------------------------------------
    def competing(self, port=None, verbose=True):
        """Processes holding the box's port - i.e. what stops you serving it.

        Returns [(pid, user, cmdline)]. A holder with pid None belongs to
        another account (the kernel hides its pid from you); find it with
        `sudo ss -ltnp | grep <port>`.
        """
        from .remote.serve import who_has_port

        port = port or self.port
        holders = who_has_port(port)
        if verbose:
            if not holders:
                print(f"nothing is listening on {port} - the port is free")
            for pid, user, cmd in holders:
                mine = " (this session)" if pid == os.getpid() else ""
                print(f"PID {pid} user {user}{mine}\n    {cmd}")
            if holders and not self.is_serving:
                print("\nNot this session. Either stop it in the session that owns it "
                      "(box.stop()), or kill the PID above.")
        return holders

    # --- the box itself, over ssh ---------------------------------------
    @property
    def target(self):
        return f"{self.user}@{self.host}"

    def ssh(self, command, timeout=20, check=False):
        """Run a command on the box and return its output as a string."""
        done = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", self.target, command],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0 and check:
            raise RuntimeError(f"ssh {self.target} failed ({done.returncode}): {out.strip()}")
        if done.returncode != 0 and "Host key verification failed" in out:
            out += (f"\n-> the box was reflashed; clear the old key with: "
                    f"ssh-keygen -R {self.host}\n   then ssh {self.target} once and accept it.")
        return out

    def box_config(self):
        """What host/port the box is configured to call, and its token file."""
        return self.ssh(f"cat /etc/eco-control-box.env; ls -l /etc/{BOX_SERVICE}.token")

    def service(self, action="status"):
        """systemctl <action> on the box's client service (status/restart/start/stop)."""
        prefix = "" if action in ("status", "is-active", "is-enabled") else "sudo -n "
        return self.ssh(f"{prefix}systemctl {action} {BOX_SERVICE} 2>&1 | head -30")

    def logs(self, lines=40, grep=None):
        """The box client's journal (add grep= to filter, e.g. 'hardware input')."""
        cmd = f"journalctl -u {BOX_SERVICE} -n {lines} --no-pager"
        if grep:
            cmd += f" | grep -i '{grep}'"
        return self.ssh(cmd, timeout=30)

    def probe(self):
        """Hardware environment report from the box (SPI, pin factory, groups).

        For the live raw joystick/encoder view, run on the box itself:
            cd /opt/eco-control-box
            python3 -m manual_control.remote.pi_hardware --probe
        """
        return self.ssh(
            f"cd {BOX_INSTALL_DIR} && python3 -m manual_control.remote.pi_hardware",
            timeout=30,
        )

    # --- eco conventions -------------------------------------------------
    def get_status(self):
        holders = self.competing(verbose=False)
        return {
            "serving": self.is_serving,
            "box_connected": self.is_connected,
            "endpoint": f"{self.bind}:{self.port}",
            "box": self.target,
            "token_file": self.token_file,
            "token_present": os.path.exists(os.path.expanduser(self.token_file)),
            "port_holders": [(pid, user) for pid, user, _ in holders],
        }

    def status(self):
        st = self.get_status()
        print(f"control box   : {st['box']}")
        print(f"serving       : {st['serving']} on {st['endpoint']}"
              f"{' - box CONNECTED' if st['box_connected'] else ''}")
        print(f"token         : {st['token_file']}"
              f"{'' if st['token_present'] else '  MISSING - the box will be rejected'}")
        if not st["serving"]:
            if st["port_holders"]:
                print(f"port {self.port} held by: " +
                      ", ".join(f"PID {p} ({u})" for p, u in st["port_holders"]) +
                      "   -> .competing()")
            else:
                print(f"port {self.port} is free   -> .start() to serve this namespace")
        print("\n.manual() prints the full manual (setup, ssh, troubleshooting).")
        return st

    def __repr__(self):
        state = "serving" if self.is_serving else "idle"
        if self.is_connected:
            state = "serving, box connected"
        return f"<ControlBox {self.target} {self.bind}:{self.port} [{state}]>"
