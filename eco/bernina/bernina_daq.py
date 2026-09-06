import os

from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent
from eco import bernina


namespace.append_obj(
    "AdjustableFS",
    # "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/config_JFs.json",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/config_JFs.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="config_JFs",
)




### channelsfor daq ###
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_JF.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_JF",
)
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_BS.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_BS",
)
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_BSCAM.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_BSCAM",
)
namespace.append_obj(
    "AdjustableFS",
    "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/channels_CA.json",
    module_name="eco.elements.adjustable",
    lazy=True,
    name="channels_CA",
)

namespace.append_obj(
    "CheckerBS",
    module_name="eco.acquisition.checkers",
    bs_channel="SAROP21-PBPS133:INTENSITY",
    thresholds=[0.2, 10],
    required_fraction=0.6,
    filepath_thresholds="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/default_checker_thresholds.json",
    filepath_fraction="/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/default_checker_thresholds_fraction.json",
    lazy=True,
    name="checker",
)

# Take run status from a long-running eco.status_server process instead of
# initializing and reading *this* session's namespace at every scan start
# (see eco/status_server/README.md). On by default, pointed at the server
# below - Daq.use_status_server() re-checks /health before every scan and
# falls back to the old local behaviour (with a printed warning) if it is
# unreachable, still initializing, or older than status_server_max_age, so a
# session never silently depends on the server being up.
#
# ECO_STATUS_SERVER is a *shell* environment variable, read once here at
# import time - it has to be set in the environment the eco session is
# started from (before `eco-dev`/`eco` runs), not from inside an already
# running IPython session, where setting it does nothing:
#   ECO_STATUS_SERVER=off scripts/eco-dev -s bernina        # this session only
#   export ECO_STATUS_SERVER=off                            # every session after
# "" / "off" / "none" / "false" / "0" (case-insensitively) force the old,
# always-local behaviour; anything else is used as the server URL instead of
# the default below.
#
# An env var rather than a key in the shared bernina config JSON on purpose:
# which host (if any) runs a status server is a per-session choice, and that
# file is read by every session at the beamline.
#
# The default below is the *personal-checkout* status server used to develop
# and test this feature (see eco/status_server/DESIGN.md) - it is not yet a
# server started from the shared gac-bernina checkout, so treat it as a dev
# deployment: fine to rely on day to day, but not yet an officially operated
# service, and it can be restarted/moved without the same notice a
# production one would get.
_ECO_STATUS_SERVER_DEFAULT = "http://saresb-cons-04:8091"
_status_server = os.environ.get("ECO_STATUS_SERVER", _ECO_STATUS_SERVER_DEFAULT)
_status_server_is_default = "ECO_STATUS_SERVER" not in os.environ
if _status_server.strip().lower() in ("", "off", "none", "false", "0"):
    _status_server = None
if _status_server:
    kind = "dev/personal-checkout" if _status_server_is_default else "configured"
    print(f"daq: taking run status from the {kind} status server "
          f"{_status_server} (set ECO_STATUS_SERVER=off in the shell before "
          "starting this session to always use the local namespace instead)")

namespace.append_obj(
    "Daq",
    instrument="bernina",
    status_server=_status_server,
    pgroup=NamespaceComponent(namespace, "config_bernina.pgroup"),
    channels_JF=NamespaceComponent(namespace, "channels_JF"),
    channels_BS=NamespaceComponent(namespace, "channels_BS"),
    channels_BSCAM=NamespaceComponent(namespace, "channels_BSCAM"),
    channels_CA=NamespaceComponent(namespace, "channels_CA"),
    config_JFs=NamespaceComponent(namespace, "config_JFs"),
    # pulse_id_adj="SLAAR21-LTIM01-EVR0:RX-PULSEID",
    pulse_id_adj="SARES20-CVME-01-EVR0:RX-PULSEID",
    event_master=NamespaceComponent(namespace, "event_master"),
    detectors_event_code=50,
    rate_multiplicator="auto",
    name="daq",
    namespace=namespace,
    checker=NamespaceComponent(namespace, "checker"),
    run_table=NamespaceComponent(namespace, "run_table"),
    pulse_picker=NamespaceComponent(namespace, "xp"),
    elog=NamespaceComponent(namespace, "elog"),
    module_name="eco.acquisition.daq_client",
    lazy=True,
)


namespace.append_obj(
    "Scans",
    # data_base_dir="scan_data",
    # scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
    # default_counters=[daq],
    default_counters=[NamespaceComponent(namespace,"daq")],
    # default_counters=NamespaceComponent(namespace,"daq"),
    callbacks_start_scan=[],
    callbacks_end_step=[],
    callbacks_end_scan=[],
    # elog=elog,
    name="scans",
    module_name="eco.acquisition.scan",
    lazy=True,
)

# namespace.append_obj(
#     "Scans",
#     # data_base_dir="scan_data",
#     # scan_info_dir=f"/sf/bernina/data/{config_bernina.pgroup()}/res/scan_info",
#     default_counters=[],
#     callbacks_start_scan=[],
#     callbacks_end_step=[],
#     callbacks_end_scan=[],
#     name="scans_test",
#     module_name="eco.acquisition.scan",
#     lazy=True,
# )
