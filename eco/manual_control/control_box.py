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

    WHO CALLS WHOM: the box LISTENS and eco sessions call it. The box
    therefore belongs to no particular console - any machine holding the
    shared token may offer itself, and the operator standing at the box
    decides. Two situations, both answered on the box's own screen:

      1. nothing connected: a session calls, the box asks Accept / Reject.
      2. already connected: another session calls, the box asks Take over /
         Keep current. Taking over tells the displaced session why, so it
         closes itself instead of lingering.

    Nothing on the box needs configuring per console, and no console needs
    ssh access to the box for normal use.

    ================================================================
    QUICK START
    ================================================================
        bernina.manual_control_box.start()      # serve this namespace
        bernina.manual_control_box.status()     # is it connected?
        bernina.manual_control_box.stop()

    `.start()` returns immediately and the session then waits for the
    operator - `.status()` shows "waiting for the operator" until someone
    taps Accept on the box, then "connected". If the box is later handed to
    another session, this one closes itself and `.status()` says why.
    `.competing()` lists which sessions are connected to the box.

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
        /etc/eco-control-box.env               LISTEN_PORT it waits on
        /etc/eco-control-box.token             shared token (permanent)
        systemd unit: eco-control-box          fullscreen app, starts at boot

    On the console side the same token must be in ~/.eco/pendant_token. It
    never expires and survives reboots and re-installs; it changes only if
    you delete the file or reflash the card. If the two ever differ, write
    any string to both and restart the server.

    ================================================================
    "THE BOX SHOWS THE WAITING SCREEN"
    ================================================================
    The box shows a dark waiting screen reading "waiting for an eco
    session", its own hostname:port (where sessions should call it) and its
    IP addresses. That screen means the box itself is healthy and nobody is
    driving it; it raises the accept dialog the moment a session calls.
    Check, in order:

    1. Did anyone offer it a session?   .start() here, then accept on the box
    2. Did the request arrive?          the box shows the accept dialog; if
                                        not, check the box can be reached:
                                        ping ecobox, and .status() will show
                                        "cannot reach the box" if not
    3. Was it declined or handed away?  .status() gives the reason this
                                        session ended
    4. Token mismatch shows on the box's journal as "rejected <ip>: bad or
       missing token", and here as a closed session with that reason.

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
    browse) and STEP (turn = step size). Stick up/down jogs the axis the
    stick is assigned to; left/right navigates. On the touchscreen, tap a
    row to enter/arm, tap a breadcrumb to jump back up, and use the -/+,
    Menu and Disarm buttons in the right-hand column.

    SEVERAL AXES AT ONCE ("slots"). Arming a second leaf does not replace
    the first: up to four stay armed, listed in the right-hand column with
    their own step size and motion mode, the one on the stick marked with
    an arrow. Beyond four, the newest replaces the one on the stick.

    THE MENU (hardware button GPIO 26, the touch "Menu" button, or the key
    m) is just another list, so the knob and finger drive it the same way -
    turn to move, press to choose, long press to go back one level:

        step size: 0.1        pick from the list (or turn the knob in
                              STEP mode, which does the same thing)
        motion: jog           jog = continuous (the IOC moves it, for a
                              MotorRecord or anything with jog()); step =
                              repeated set_target_value hops of step size
        stick axis: theta     which armed axis the joystick drives
        memories              this adjustable's stored memories: pick one
                              to recall (with a YES confirmation, since it
                              moves hardware), or save the current state
        release <name>        drop this axis from the slots

    Step sizes are remembered per adjustable across sessions, in
    ~/.eco/manual_control_steps.json. A first-time axis takes its step from
    the object's own `tweak` interval when it has one, else the middle of
    the box's step list.

    Motors with a native jog (MotorRecord, via JOGF/JOGR - and anything
    decorated with `jog_option`) are jogged by the IOC itself; everything
    else is stepped repeatedly by the console, so the link never makes
    jogging stutter. "motion: step" forces the stepped behaviour even on
    an axis that could jog continuously.

    ================================================================
    RUNNING THIS AS A BACKGROUND SERVICE (no session left open)
    ================================================================
    Everything above works from inside any eco session. If you would
    rather not keep an interactive session open just to hold the box
    connection, eco.manual_control.box_server does the same thing
    (namespace.start_eco_control_box()) as a small standalone process with
    its own admin/health HTTP API - mirrors eco-status-server's shape.

        eco-box-server start [-b]    # foreground, or -b to detach
        eco-box-server status        # state, box, reason, process stats
        eco-box-server disconnect    # the safety button
        eco-box-server reconnect     # offer a session again (box must accept)
        eco-box-server restart       # re-exec to pick up new device code
        eco-box-server gui           # Qt monitor/control panel (same actions)
        eco-box-server stop

    Same env-var-overridable configuration as eco-status-server
    (ECO_BOX_SERVER_CHECKOUT/SCOPE/BOX_HOST/BOX_PORT/TOKEN_FILE/...) - see
    the script's own header, or `eco-box-server config`.

    From a checkout, for quick local testing against uncommitted device
    changes: `eco-dev box-server -s bernina` runs the same module in the
    foreground against *this* checkout, no wrapper script involved.

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

    def start(self, namespace=None, port=None, token=None, **box_kwargs):
        """Offer a namespace to the box; the operator there accepts it."""
        from .remote.serve import connect_to_box

        if self.is_serving:
            print(f"already connected: {self.server!r}")
            return self.server
        ns = namespace if namespace is not None else self._resolve_namespace()
        self.server = connect_to_box(
            ns, root_name=getattr(ns, "name", None), host=self.host,
            port=port or self.port, token=token, token_file=self.token_file,
            **box_kwargs,
        )
        return self.server

    def stop(self):
        """Disconnect from the box (it falls back to its waiting screen)."""
        if self.server is None:
            print("no control box session running here")
            return
        self.server.stop()
        self.server = None
        print("control box session stopped")

    @property
    def state(self):
        """dialling / waiting for the operator / connected / closed."""
        return "idle" if self.server is None else self.server.state

    @property
    def is_serving(self):
        return self.server is not None and not self.server._stop.is_set()

    @property
    def is_connected(self):
        return bool(self.server is not None and self.server.connected)

    # --- who else is serving -------------------------------------------
    def competing(self, verbose=True):
        """Which eco sessions are currently connected to the box.

        The box decides who drives it, so competition is resolved there, not
        by a contested port here: a second session simply asks the operator
        to hand it over. This lists the established connections on the box
        (needs ssh to it; harmless if that is unavailable).
        """
        out = self.ssh(f"ss -tnH state established '( sport = :{self.port} )' 2>/dev/null")
        peers = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                peers.append(parts[3])
        if verbose:
            if peers:
                print("sessions connected to the box: " + ", ".join(peers))
            elif "Host key verification failed" in out or "Permission denied" in out:
                print(f"cannot ask the box over ssh:\n{out.strip()}")
            else:
                print("no eco session is connected to the box")
            if self.is_serving:
                print(f"this session: {self.server!r}")
        return peers

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
        return {
            "state": self.state,
            "box": self.target,
            "endpoint": f"{self.host}:{self.port}",
            "connected": self.is_connected,
            "reason": getattr(self.server, "reason", None),
            "token_file": self.token_file,
            "token_present": os.path.exists(os.path.expanduser(self.token_file)),
        }

    def status(self):
        st = self.get_status()
        print(f"control box   : {st['box']}   (calling {st['endpoint']})")
        print(f"this session  : {st['state']}"
              f"{'' if not st['reason'] else '  - ' + st['reason']}")
        print(f"token         : {st['token_file']}"
              f"{'' if st['token_present'] else '  MISSING - the box will reject this session'}")
        if st["state"] == "idle":
            print("\n.start() offers this namespace to the box; accept it on the box itself.")
        elif st["state"] == "waiting for the operator":
            print("\nTap Accept on the box to hand it to this session.")
        print("\n.manual() prints the full manual (setup, ssh, troubleshooting).")
        return st

    def __repr__(self):
        return f"<ControlBox {self.target} {self.host}:{self.port} [{self.state}]>"
