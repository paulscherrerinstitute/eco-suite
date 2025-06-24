from eco import Assembly
from eco.elements.detector import DetectorGet
from eco.elements.adjustable import AdjustableGetSet
from .PBComm import SSHComm
from .powerbrick_parameters import *

connected_power_bricks = {}


def get_power_brick_comm(hostname):
    if hostname in connected_power_bricks.keys():
        return connected_power_bricks[hostname]
    else:
        return PowerBrickComm(hostname)


class PowerBrickComm:
    def __init__(self, hostname):
        self.pbsshcom = SSHComm()
        self.pbsshcom.connect(hostname)
        connected_power_bricks[hostname] = self

    def get_parameter(self, parstring, autoconvert=True):
        if isinstance(parstring, bytes):
            parstring = parstring.decode()
        if not parstring.endswith("\n"):
            parstring += "\n"
        self.pbsshcom.chan.send(parstring)
        par = self.pbsshcom.read_until_endswith(SSHComm.gpascii_ack)
        par = par.split(SSHComm.gpascii_ack)[0]
        # print(par)
        if autoconvert:
            par = par.lower().split(parstring.lower().strip("\n"))[1]
            if par.startswith("="):
                par = par[1:]
            try:
                par = int(par)
            except ValueError:
                try:
                    par = float(par)
                except ValueError:
                    pass
        return par

    def set_parameter(self, value, parstring):
        if isinstance(parstring, bytes):
            parstring = parstring.decode()
        if parstring.endswith("\n"):
            parstring.strip("\n")
        setstring = parstring + "=" + str(value)
        return self.pbsshcom.iawrite(setstring)
    
    # def set_hmz


class PowerBrickChannelPars(Assembly):
    def __init__(self, hostname, axis=None, type=None, pars_list=[], name=None):
        super().__init__(name=name)
        self._pbcom = get_power_brick_comm(hostname)
        self._ch = axis

        if type == "motor":
            pars_list = pars_motors

        else:
            pass

        for par, mode in pars_list:
            if "r" in mode and "w" not in mode:
                self.append_par_detector(par)
            elif "r" in mode and "w" in mode:
                # print(par)
                self.append_par_adjustable(par)

    def get_from_par(self, formstr, **kwargs):
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
