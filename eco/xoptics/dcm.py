from ..devices_general.motors import MotorRecord, MotorRecord_new
from ..devices_general.pv_adjustable import PvRecord
from epics import PV
from ..devices_general.utilities import Changer
from time import sleep
import numpy as np
from ..aliases import Alias, append_object_to_object
from ..elements.adjustable import spec_convenience, default_representation, tweak_option
from ..epics.adjustable import AdjustablePvEnum, AdjustablePvString
from ..devices_general.utilities import Changer
from ..elements.assembly import Assembly


def addMotorRecordToSelf(self, Id=None, name=None):
    try:
        self.__dict__[name] = MotorRecord(Id, name=name)
        self.alias.append(self.__dict__[name].alias)
    except:
        print(f"Warning! Could not find motor {name} (Id:{Id})")


def addPvToSelf(self, Id=None, name=None):
    try:
        self.__dict__[name] = PV(Id)
        self.alias.append(Alias(name, channel=Id, channeltype="CA"))
    except:
        print(f"Warning! Could not find PV {name} (Id:{Id})")


def addPvRecordToSelf(
    self, pvsetname, pvreadbackname=None, accuracy=None, sleeptime=0, name=None
):
    try:
        self.__dict__[name] = PvRecord(
            pvsetname,
            pvreadbackname=pvreadbackname,
            accuracy=accuracy,
            sleeptime=sleeptime,
            name=name,
        )
        self.alias.append(self.__dict__[name].alias)

    except:
        print(f"Warning! Could not find PV {name} (Id:{pvsetname} RB:{pvreadbackname})")


class DoubleCrystalMono(Assembly):
    def __init__(self, pvname, name=None, energy_sp=None, energy_rb=None):
        super().__init__(name=name)
        self.pvname = pvname
        self._append(MotorRecord_new, pvname + ":RX12", name="theta")
        self._append(MotorRecord_new, pvname + ":TX12", name="x")
        self._append(MotorRecord_new, pvname + ":T2", name="gap")
        self._append(MotorRecord_new, pvname + ":RZ1", name="roll1")
        self._append(MotorRecord_new, pvname + ":RZ2", name="roll2")
        self._append(MotorRecord_new, pvname + ":RX2", name="pitch2")
        self._append(
            PvRecord,
            pvsetname=energy_sp,
            pvreadbackname=energy_rb,
            accuracy=0.5,
            name="energy",
        )
        self.moving = PV(Id + ":MOVING")
        self._stop = PV(Id + ":STOP.PROC")

    def move_and_wait(self, value, checktime=0.01, precision=0.5):
        self.energy.set_target_value(value)
        while abs(self.wait_for_valid_value() - value) > precision:
            sleep(checktime)

    def set_target_value(self, value, hold=False):
        changer = lambda value: self.move_and_wait(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=self.stop
        )

    def stop(self):
        self._stop.put(1)

    def get_current_value(self):
        currentenergy = self.energy.get_current_value()
        return currentenergy

    def wait_for_valid_value(self):
        tval = np.nan
        while not np.isfinite(tval):
            tval = self.energy.get_current_value()
        return tval

    def set_current_value(self, value):
        self.energy.set_current_value(value)

    def get_moveDone(self):
        inmotion = int(self.moving.get())
        return inmotion

    # spec-inspired convenience methods
    def mv(self, value):
        self._currentChange = self.set_target_value(value)

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    def mvr(self, value, *args, **kwargs):

        if self.get_moveDone == 1:
            startvalue = self.get_current_value(*args, **kwargs)
        else:
            startvalue = self.get_current_value(*args, **kwargs)
        self._currentChange = self.set_target_value(value + startvalue, *args, **kwargs)

    def wait(self):
        self._currentChange.wait()

    def __str__(self):
        s = "**Double crystal monochromator**\n\n"
        motors = "theta gap x roll1 roll2 pitch2 energy".split()
        for motor in motors:
            s += " - %s = %.4f\n" % (motor, getattr(self, motor).get_current_value())
        return s

    def __repr__(self):
        return self.__str__()

    def __call__(self, value):
        self._currentChange = self.set_target_value(value)


class Double_Crystal_Mono:
    def __init__(self, Id, name=None, energy_sp=None, energy_rb=None):
        self.Id = Id
        self.name = name
        self.alias = Alias(name)
        addMotorRecordToSelf(self, Id=Id + ":RX12", name="theta")
        addMotorRecordToSelf(self, Id=Id + ":TX12", name="x")
        addMotorRecordToSelf(self, Id=Id + ":T2", name="gap")
        addMotorRecordToSelf(self, Id=Id + ":RZ1", name="roll1")
        addMotorRecordToSelf(self, Id=Id + ":RZ2", name="roll2")
        addMotorRecordToSelf(self, Id=Id + ":RX2", name="pitch2")
        if energy_sp:
            addPvRecordToSelf(
                self,
                pvsetname=energy_sp,
                pvreadbackname=energy_rb,
                accuracy=0.5,
                name="energy",
            )
        else:
            addPvRecordToSelf(
                self,
                pvsetname=Id + ":ENERGY_SP",
                pvreadbackname=Id + ":ENERGY",
                accuracy=0.2,
                name="energy",
            )
        self.moving = PV(Id + ":MOVING")
        self._stop = PV(Id + ":STOP.PROC")

    def move_and_wait(self, value, checktime=0.01, precision=0.5):
        self.energy.set_target_value(value)
        while abs(self.wait_for_valid_value() - value) > precision:
            sleep(checktime)

    def set_target_value(self, value, hold=False):
        changer = lambda value: self.move_and_wait(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=self.stop
        )

    def stop(self):
        self._stop.put(1)

    def get_current_value(self):
        currentenergy = self.energy.get_current_value()
        return currentenergy

    def wait_for_valid_value(self):
        tval = np.nan
        while not np.isfinite(tval):
            tval = self.energy.get_current_value()
        return tval

    def set_current_value(self, value):
        self.energy.set_current_value(value)

    def get_moveDone(self):
        inmotion = int(self.moving.get())
        return inmotion

    # spec-inspired convenience methods
    def mv(self, value):
        self._currentChange = self.set_target_value(value)

    def wm(self, *args, **kwargs):
        return self.get_current_value(*args, **kwargs)

    def mvr(self, value, *args, **kwargs):

        if self.get_moveDone == 1:
            startvalue = self.get_current_value(*args, **kwargs)
        else:
            startvalue = self.get_current_value(*args, **kwargs)
        self._currentChange = self.set_target_value(value + startvalue, *args, **kwargs)

    def wait(self):
        self._currentChange.wait()

    def __str__(self):
        s = "**Double crystal monochromator**\n\n"
        motors = "theta gap x roll1 roll2 pitch2 energy".split()
        for motor in motors:
            s += " - %s = %.4f\n" % (motor, getattr(self, motor).get_current_value())
        return s

    def __repr__(self):
        return self.__str__()

    def __call__(self, value):
        self._currentChange = self.set_target_value(value)


@spec_convenience
@default_representation
@tweak_option
class EcolEnergy_new(Assembly):
    def __init__(
        self,
        pv_val="SARCL02-MBND100:USER-ENE",
        pv_enable="SARCL02-MBND100:USER-ENA",
        pv_rb="SARCL02-MBND100:P-READ",
        pv_diff="SARCL02-MBND100:USER-ERROR",
        name=None,
    ):
        super().__init__(name=name)
        self._append(AdjustablePvEnum, pv_enable, name="enable_control")
        self._pv_val = PV(pv_val)
        self._pv_rb = PV(pv_rb)
        self._pv_diff = PV(pv_diff)
        self._append(
            AdjustablePvString,
            pv_rb + ".EGU",
            name="unit",
            is_setting=False,
            is_display=False,
        )

    def change_energy_to(self, value, tolerance=0.5):
        self.enable_control(0)
        sleep(0.1)
        self._pv_val.put(value)
        sleep(0.1)
        self.enable_control(1)
        done = False
        sleep(0.1)
        while not done:
            sleep(0.05)
            diffabs = np.abs(self._pv_rb.get() - value)
            # diff = self._pv_diff.get()
            if diffabs < tolerance:
                diff = self._pv_diff.get()
                if diff == 0:
                    done = True
        self.enable_control(0)

    def get_current_value(self):
        return self._pv_rb.get()

    def set_target_value(self, value, hold=False):
        """Adjustable convention"""

        changer = lambda value: self.change_energy_to(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )


class EcolEnergy:
    def __init__(
        self,
        Id,
        val="SARCL02-MBND100:P-SET",
        rb="SARCL02-MBND100:P-READ",
        dmov="SFB_BEAM_ENERGY_ECOL:SUM-ERROR-OK",
        name=None,
    ):
        self.Id = Id
        self.setter = PV(val)
        self.readback = PV(rb)
        # self.dmov = PV(dmov)
        self.done = False
        self.name = name
        self.alias = Alias(name)

    def get_current_value(self):
        return self.readback.get()

    def move_and_wait(self, value, checktime=0.01, precision=2):
        curr = self.setter.get()
        while abs(curr - value) > 0.1:
            curr = self.setter.get()
            self.setter.put(curr + np.sign(value - curr) * 0.1)
            sleep(0.3)

        self.setter.put(value)
        while abs(self.get_current_value() - value) > precision:
            sleep(checktime)
        # while not self.dmov.get():
        #     # print(self.dmov.get())
        #     sleep(checktime)

    def set_target_value(self, value, hold=False):
        changer = lambda value: self.move_and_wait(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )


class MonoEcolEnergy:
    def __init__(self, Id):
        self.Id = Id
        self.name = "energy_collimator"
        self.dcm = Double_Crystal_Mono(Id)
        self.ecol = EcolEnergy("ecol_dummy")
        self.offset = None
        self.MeVperEV = 0.78333

    def get_current_value(self):
        return self.dcm.get_current_value()

    def move_and_wait(self, value):
        ch = [
            self.dcm.set_target_value(value),
            self.ecol.set_target_value(self.calcEcol(value)),
        ]
        for tc in ch:
            tc.wait()

    def set_target_value(self, value, hold=False):
        changer = lambda value: self.move_and_wait(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=self.dcm.stop
        )

    def alignOffsets(self):
        mrb = self.dcm.get_current_value()
        erb = self.ecol.get_current_value()
        self.offset = {"dcm": mrb, "ecol": erb}

    def calcEcol(self, eV):
        return (eV - self.offset["dcm"]) * self.MeVperEV + self.offset["ecol"]


class AlvraDCM_FEL:
    def __init__(self, Id):
        self.Id = Id
        self.name = "Alvra DCM monochromator coupled to FEL beam"
        # 		self.IOCstatus = PV('ALVRA:running')					# bool 0 running, 1 not running
        self._FELcoupling = PV("SGE-OP2E-ARAMIS:MODE_SP")  # string "Off" or "e-beam"
        self._setEnergy = PV("SAROP11-ARAMIS:ENERGY_SP_USER")  # float eV
        #self._getEnergy = PV("SAROP11-ARAMIS:ENERGY")  # float eV
        self._getEnergy = PV("SAROP21-ODCM098:ENERGY")  # float eV
        self.ebeamEnergy = PV("SARCL02-MBND100:P-READ")  # float MeV/c
        # 		self.ebeamEnergySP = PV('ALVRA:Energy_SP')				# float MeV
        self.dcmStop = PV("SAROP11-ODCM105:STOP.PROC")  # stop the DCM motors
        self.dcmMoving = PV("SAROP11-ODCM105:MOVING")  # DCM moving field
        self._energyChanging = PV(
            "SGE-OP2E-ARAMIS:MOVING"
        )  # PV telling you something related to the energy is changing
        self._alvraMode = PV("SAROP11-ARAMIS:MODE")  # string Aramis SAROP11 mode
        self.ebeamOK = PV(
            "SFB_BEAM_ENERGY_ECOL:SUM-ERROR-OK"
        )  # is ebeam no longer changing
        self.photCalib1 = PV(
            "SGE-OP2E-ARAMIS:PH2E_X1"
        )  # photon energy calibration low calibration point
        self.photCalib2 = PV(
            "SGE-OP2E-ARAMIS:PH2E_X2"
        )  # photon energy calibration high calibration point
        self.ebeamCalib1 = PV(
            "SGE-OP2E-ARAMIS:PH2E_Y1"
        )  # electron energy calibration low calibration point
        self.ebeamCalib2 = PV(
            "SGE-OP2E-ARAMIS:PH2E_Y2"
        )  # electron energy calibration high calibration point

    def __str__(self):
        # 		ioc = self.IOCstatus.get()
        # 		if ioc == 0:
        # 			iocStr = "Soft IOC running"
        # 		else:
        # 			iocStr = "Soft IOC not running"
        FELcouplingStr = self._FELcoupling.get(as_string=True)
        alvraModeStr = self._alvraMode.get(as_string=True)
        currEnergy = self._getEnergy.get()
        currebeamEnergy = self.ebeamEnergy.get()
        photCalib1Str = self.photCalib1.get()
        photCalib2Str = self.photCalib2.get()
        ebeamCalib1Str = self.ebeamCalib1.get()
        ebeamCalib2Str = self.ebeamCalib2.get()

        s = "**Alvra DCM-FEL status**\n\n"
        # 		print('%s'%iocStr)
        #  		print('FEL coupling %s'%FELcouplingStr)
        #  		print('Alvra beamline mode %s'%alvraModeStr)
        #  		print('Photon energy (eV) %'%currEnergy)
        # 		s += '%s\n'%iocStr
        s += "FEL coupling: %s\n" % FELcouplingStr
        s += "Alvra beamline mode: %s\n" % alvraModeStr
        s += "Photon energy: %.2f eV\n" % currEnergy
        s += "Electron energy: %.2f MeV\n" % currebeamEnergy
        s += "Calibration set points:\n"
        s += "Low: Photon %.2f keV, Electron %.2f MeV\n" % (
            photCalib1Str,
            ebeamCalib1Str,
        )
        s += "High: Photon %.2f keV, Electron %.2f MeV\n" % (
            photCalib2Str,
            ebeamCalib2Str,
        )
        return s

    def get_current_value(self):
        return self._getEnergy.get()

    def move_and_wait(self, value, checktime=0.1, precision=0.5):
        self._FELcoupling.put(1)  # ensure the FEL coupling is turned on
        self._setEnergy.put(value)
        # 		while self.ebeamOK.get()==0:
        # 			sleep(checktime)
        # 		while abs(self.ebeamEnergy.get()-self.ebeamEnergySP.get())>precision:
        # 			sleep(checktime)
        # 		while self.dcmMoving.get()==1:
        # 			sleep(checktime)
        while self._energyChanging == 1:
            sleep(checktime)

    def set_target_value(self, value, hold=False):
        changer = lambda value: self.move_and_wait(value)
        return Changer(
            target=value, parent=self, changer=changer, hold=hold, stopper=None
        )

    def __repr__(self):
        return self.__str__()
