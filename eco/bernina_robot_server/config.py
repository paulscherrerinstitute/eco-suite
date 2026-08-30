"""Server configuration.

Replaces pshell's ``config.properties``/``setup.properties`` pair. Only the
settings that actually mattered for this deployment survive -- the pshell files
carried ~90 keys, of which the robot server used the server port, the terminal
port, and the paths.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: The pshell deployment's persisted adjustables. Reused as-is so a migrated
#: server inherits the recorded remote-motion trajectories.
DEFAULT_ADJUSTABLES_PATH = (
    "/sf/bernina/config/src/python/bernina_robot/adjustables_fs/"
)


@dataclass
class RobotServerConfig:
    # --- controller ---------------------------------------------------
    #: The VAL3 TCP dispatcher. Was hard-coded in RobotBernina.py.
    robot_host: str = "129.129.243.106"
    robot_port: int = 1234
    #: Per-call reply timeout, seconds. Also bounds abort latency.
    robot_timeout: float = 1.0
    robot_retries: int = 1
    #: Minimum spacing between frames; 0 disables throttling.
    robot_latency: float = 0.0
    #: Run against a fake controller instead of hardware.
    simulated: bool = False
    #: Persist frame/tool/recorded-motion state to ``adjustables_path``.
    #: None means "not when simulated" -- see connect_bernina_robot(); a
    #: simulated run must not rewrite the shared production files, which live
    #: under the gac-bernina account.
    persist_state: bool | None = None

    # --- polling ------------------------------------------------------
    polling_interval: float = 0.2
    env_polling_interval: float = 1.0

    # --- HTTP ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080

    # --- EPICS --------------------------------------------------------
    epics_enabled: bool = True
    epics_prefix: str = "SARES20-ROB:"

    # --- behaviour ----------------------------------------------------
    adjustables_path: str = DEFAULT_ADJUSTABLES_PATH
    #: Start with the recorded-trajectory whitelist disabled. Operator choice,
    #: normally False -- see BerninaRobot.remote_allowed.
    override_remote_safety: bool = False
    #: Enable motor groups at startup, as RobotBernina.setup() did.
    setup_on_start: bool = True
    #: Seconds a finished command's result is retained for /result/<id>.
    command_time_to_live: float = 600.0
    #: Concurrent background ("cmd&") commands.
    max_background_commands: int = 8
    #: Names exposed to the /eval endpoint beyond the robot and its motors.
    log_ring_size: int = 500

    def __post_init__(self):
        self.robot_port = int(self.robot_port)
        self.port = int(self.port)

    @classmethod
    def from_file(cls, path):
        data = json.loads(Path(path).read_text())
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"unknown config keys in {path}: {sorted(unknown)}; "
                f"valid keys are {sorted(known)}"
            )
        return cls(**data)

    def to_dict(self):
        return asdict(self)
