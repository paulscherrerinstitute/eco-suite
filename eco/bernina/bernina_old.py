from .config import elog as _elog_info
from ..utilities.elog import Elog as _Elog
from ..utilities.elog import Screenshot as _Screenshot
from ..utilities.config import loadConfig
from epics import PV
import sys, os


from colorama import Fore as _color
import traceback

elog = _Elog(
    _elog_info["url"],
    user="gac-bernina",
    screenshot_directory=_elog_info["screenshot_directory"],
)
screenshot = _Screenshot(screenshot_directory=_elog_info["screenshot_directory"])

###########  configurations  ########################
currexp_file_path = "/sf/bernina/config/exp/current_experiment.json"
if os.path.exists(currexp_file_path):
    exp_config = loadConfig(currexp_file_path)
else:
    print("NB: Could not load experiment config in path %s ." % currexp_file_path)

########### GENERAL IMPLEMENTATIONS ##################
from .config import components_old
from ..utilities.utilities_instruments import initDeviceAliasList

dev, problems = initDeviceAliasList(components_old, lazy=True, verbose=True)

if 0:

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
            print(sys.exc_info())
            error = e
        return error

    break
    errors = []
    for device_Id in _aliases.keys():
        if "eco_type" in _aliases[device_Id].keys() and _aliases[device_Id]["eco_type"]:
            if "args" in _aliases[device_Id].keys() and _aliases[device_Id]["args"]:
                args = _aliases[device_Id]["args"]
            else:
                args = tuple()

            if "kwargs" in _aliases[device_Id].keys() and _aliases[device_Id]["kwargs"]:
                kwargs = _aliases[device_Id]["kwargs"]
            else:
                kwargs = dict()

            e = _attach_device(_aliases[device_Id], device_Id, args, kwargs)
            if e:
                errors.append((_aliases[device_Id]["alias"], e))

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
    from ..acquisition.ioxos_data import Ioxostools

    channellist = dict(
        bernina_channel_list=parseChannelListFile(
            "/sf/bernina/config/channel_lists/default_channel_list"
        )
    )
    bsdaq = BStools(default_channel_list=channellist, default_file_path="%s")

    channellistioxos = dict(
        bernina_channel_list=parseChannelListFile(
            "/sf/bernina/config/channel_lists/default_channel_list_ioxos"
        )
    )
    ioxosdaq = Ioxostools(default_channel_list=channellistioxos, default_file_path="%s")

    # from eco.devices_general.detectors import JF_BS_writer
    # bsdaqJF = JF_BS_writer('bsdaqJF') d
    from eco.devices_general.detectors import DIAClient

    bsdaqJF = DIAClient(
        "bsdaqJF", instrument="bernina", api_address="http://sf-daq-1:10000"
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
    checker["args"] = [[100, 700]]
    checker["kwargs"] = {}
    checker["wait_time"] = 3

    scansIoxos = _scan.Scans(
        data_base_dir="/sf/bernina/config/com/data/scan_data",
        scan_info_dir="/sf/bernina/config/com/data/scan_info",
        default_counters=[ioxosdaq],
    )
    scansJF = _scan.Scans(
        data_base_dir="scan_data",
        scan_info_dir="/sf/bernina/data/%s/res/scan_info" % exp_config["pgroup"],
        default_counters=[bsdaqJF],
        checker=checker,
        scan_directories=True,
    )
    scansBsreadLocal = _scan.Scans(
        data_base_dir="/sf/bernina/config/com/data/scan_data",
        scan_info_dir="/sf/bernina/config/com/data/scan_info",
        default_counters=[bsdaq],
    )

    ###########  ADHOC IMPLEMENTED  ########################
    bsdaqJF.gain_file = "/sf/bernina/data/p16582/res/gains_I0.h5"
    try:
        import glob

        path = "/sf/bernina/data/p17295/res/JF_pedestal/pedestal_*_res.h5"
        list_of_files = glob.glob(
            "/sf/bernina/data/p17295/res/JF_pedestal/pedestal_*_res.h5"
        )
        latest_file = max(list_of_files, key=os.path.getctime)
        bsdaqJF.pede_file = latest_file
    except (Exception, ArithmeticError) as e:
        template = "An exception of type {0} occurred when trying to load lates JF files from {2}. Arguments:\n{1!r}"
        message = template.format(type(e).__name__, e.args, path)
        print(message)
    from ..timing.lasertiming import Lxt as _Lxt

    lxt = _Lxt()

    from ..xoptics.dcm import MonoEcolEnergy, EcolEnergy

    monoEcol = MonoEcolEnergy("SAROP21-ODCM098")
    ecol = EcolEnergy("dummy")

    if "eco_path" in list(exp_config.keys()):
        pass
    else:
        eco_path = "/sf/bernina/" + exp_config["pgroup"]
