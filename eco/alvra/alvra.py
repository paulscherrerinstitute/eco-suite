from .config import elog as _elog_info
from ..utilities.elog import Elog as _Elog
from ..utilities.elog import Screenshot as _Screenshot
from ..utilities.config import loadConfig
from epics import PV
import sys, os


from colorama import Fore as _color
import traceback
import datetime

elog = _Elog(
    _elog_info["url"],
    user="gac-alvra",
    screenshot_directory=_elog_info["screenshot_directory"],
)
screenshot = _Screenshot(screenshot_directory=_elog_info["screenshot_directory"])
# stationMessage = _stationMessage('ESA')

_smaractappends = []

###########  configurations  ########################
currexp_file_path = "/sf/alvra/config/exp/current_experiment.json"
if os.path.exists(currexp_file_path):
    exp_config = loadConfig(currexp_file_path)
else:
    print("NB: Could not load experiment config in path %s ." % currexp_file_path)

########### GENERAL IMPLEMENTATIONS ##################
from .config import aliases as _aliases


def _attach_device(devDict, devId, args, kwargs):
    imp_p = devDict["eco_type"].split(sep=".")
    dev_alias = devDict["alias"]
    dev_alias = dev_alias[0].lower() + dev_alias[1:]
    eco_type_name = imp_p[-1]
    istr = "from .." + ".".join(imp_p[:-1]) + " import "
    istr += "%s as _%s" % (eco_type_name, eco_type_name)
    # print(istr)
    print(("Configuring %s " % (dev_alias)).ljust(25), end="")
    print(("(%s)" % (devId)).ljust(25), end="")
    error = None
    try:
        exec(istr)
        tdev = eval("_%s(Id='%s',*args,**kwargs)" % (eco_type_name, devId))
        tdev.name = dev_alias
        tdev._z_und = devDict["z_und"]
        globals().update([(dev_alias, tdev)])
        print((_color.GREEN + "OK" + _color.RESET).rjust(5))
    except Exception as e:
        print((_color.RED + "FAILED" + _color.RESET).rjust(5))
        error = e
    return error


errors = []
_composed = {}
for device_Id in _aliases.keys():
    alias = _aliases[device_Id]
    if "eco_type" in alias.keys() and alias["eco_type"]:
        if "args" in alias.keys() and alias["args"]:
            args = alias["args"]
        else:
            args = tuple()

        if "kwargs" in alias.keys() and alias["kwargs"]:
            kwargs = alias["kwargs"]
        else:
            kwargs = dict()

        e = _attach_device(alias, device_Id, args, kwargs)
        if e:
            errors.append((alias["alias"], e))
        else:
            _device = globals()[alias["alias"][0].lower() + alias["alias"][1:]]
            if hasattr(_device, "_smaractaxes"):
                _smaractappends.append(_device)
            else:
                if (
                    alias["eco_type"].endswith("SmarActRecord")
                    and "device" in alias
                    and "axis" in alias
                ):
                    if alias["device"] not in _composed:
                        _composed[alias["device"]] = {}
                    _composed[alias["device"]][alias["axis"]] = globals()[
                        alias["alias"]
                    ]

print("Integrating SmarAct devices:")

for _device in _smaractappends:
    print(
        "  Appending %s: %s"
        % (_device.name, ", ".join(map(str, list(_device._smaractaxes.keys()))))
    )
    for _axis in _device._smaractaxes.keys():
        setattr(_device, _axis, globals()[_device._smaractaxes[_axis]])

from ..devices_general.smaract import SmarActStage as _SmarActStage

for _key in _composed.keys():
    print(
        "  Composing %s: %s" % (_key, ", ".join(map(str, list(_composed[_key].keys()))))
    )
    globals()[_key] = _SmarActStage(_composed[_key], _key)

if len(errors) > 0:
    print("Found errors when configuring %s" % [te[0] for te in errors])
    if input("Would you like to see error traces? (y/n)") == "y":
        for error in errors:
            print("---> Error when configuring %s" % error[0])
            traceback.print_tb(error[1].__traceback__)


########### DAQ SECTION  ########################
# configuring bs daq


def parseChannelListFile(fina):
    out = []
    with open(fina, "r") as f:
        done = False
        while not done:
            d = f.readline()
            if not d:
                done = True
            if len(d) > 0:
                if not d.isspace():
                    if not d[0] == "#":
                        out.append(d.strip())
    return out


from ..acquisition.bs_data import BStools
from ..acquisition import scan as _scan

# from ..acquisition.ioxos_data import Ioxostools

channellist = dict(
    alvra_channel_list=parseChannelListFile(
        "/sf/alvra/config/com/channel_lists/default_channel_list"
    )
)
bsdaq = BStools(default_channel_list=channellist, default_file_path="%s")

# channellistioxos = dict(alvra_channel_list=
#        parseChannelListFile('/sf/alvra/config/com/channel_lists/default_channel_list_ioxos'))
# ioxosdaq = Ioxostools(default_channel_list=channellistioxos,default_file_path='%s')

channellistPhotonDiag = dict(
    alvra_channel_list=parseChannelListFile(
        "/sf/alvra/config/com/channel_lists/default_channel_list_PhotonDiag"
    )
)
bsdaqPhotonDiag = BStools(
    default_channel_list=channellistPhotonDiag, default_file_path="%s"
)

from eco.devices_general.alvradetectors import DIAClient

bsdaqJF = DIAClient(
    "bsdaqJF",
    instrument="alvra",
    api_address="http://sf-daq-alvra:10000",
    jf_name="JF_4.5M",
)

try:
    bsdaqJF.pgroup = int(exp_config["pgroup"][1:])
except:
    print("Could not set p group in bsdaqJF !!")

checkerPV = PV("SARFE10-PBPG050:HAMP-INTENSITY-CAL")


def checker_function(limits):
    cv = checkerPV.get()
    if cv > limits[0] and cv < limits[1]:
        return True
    else:
        return False


checker = {}
checker["checker_call"] = checker_function
checker["args"] = [[100, 300]]
checker["kwargs"] = {}
checker["wait_time"] = 3


# scansIoxos = _scan.Scans(data_base_dir='/sf/alvra/config/com/data/scan_data',scan_info_dir='/sf/alvra/config/com/data/scan_info',default_counters=[ioxosdaq])
scansJF = _scan.Scans(
    data_base_dir="scan_data",
    scan_info_dir="/sf/alvra/data/%s/res/scan_info" % exp_config["pgroup"],
    default_counters=[bsdaqJF],
    checker=checker,
    scan_directories=True,
)
scansBsreadLocal = _scan.Scans(
    data_base_dir="/sf/alvra/config/com/data/scan_data",
    scan_info_dir="/sf/alvra/config/com/data/scan_info",
    default_counters=[bsdaq],
)

scansPhotonDiag = _scan.Scans(
    data_base_dir="/sf/alvra/config/com/data/photon_diag/scan_data",
    scan_info_dir="/sf/alvra/config/com/data/photon_diag/scan_info",
    default_counters=[bsdaqPhotonDiag],
)

###########  ADHOC IMPLEMENTED  ########################
from ..timing.alvralasertiming import Lxt as _Lxt

lxt = _Lxt()

from ..xoptics.dcm import AlvraDCM_FEL

monoFEL = AlvraDCM_FEL("SAROP11-ODCM105")
