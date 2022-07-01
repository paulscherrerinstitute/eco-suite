from eco.elements.assembly import Assembly
from eco.loptics.position_monitors import CameraPositionMonitor
from ..aliases import Alias
from ..devices_general.motors import MotorRecord, SmaractStreamdevice
from ..devices_general.smaract import SmarActRecord

from epics import PV
from ..devices_general.delay_stage import DelayStage
from ..elements.adjustable import AdjustableVirtual
from ..devices_general.pv_adjustable import PvRecord

import colorama, datetime
from pint import UnitRegistry
from time import sleep

ureg = UnitRegistry()


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


def addMotorRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = MotorRecord(pvname=Id, name=name)
    self.alias.append(self.__dict__[name].alias)


def addSmarActRecordToSelf(self, Id=None, name=None):
    self.__dict__[name] = SmaractStreamdevice(Id, name=name)
    self.alias.append(self.__dict__[name].alias)


def addDelayStageToSelf(self, stage=None, name=None):
    self.__dict__[name] = DelayStage(stage, name=name)
    self.alias.append(self.__dict__[name].alias)


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


class DelayTime(AdjustableVirtual):
    def __init__(
        self, stage, direction=1, passes=2, reset_current_value_to=True, name=None
    ):
        self._direction = direction
        self._group_velo = 299798458  # m/s
        self._passes = passes
        # self.Id = stage.Id + "_delay"
        self._stage = stage
        AdjustableVirtual.__init__(
            self,
            [stage],
            self._mm_to_s,
            self._s_to_mm,
            reset_current_value_to=reset_current_value_to,
            name=name,
        )
        addPvRecordToSelf(
            self,
            pvsetname="SIN-TIMAST-TMA:Evt-22-Freq-SP",
            pvreadbackname="SIN-TIMAST-TMA:Evt-22-Freq-I",
            accuracy=0.5,
            name="frequency_pp",
        )
        addPvRecordToSelf(
            self,
            pvsetname="SIN-TIMAST-TMA:Evt-27-Freq-SP",
            pvreadbackname="SIN-TIMAST-TMA:Evt-27-Freq-I",
            accuracy=0.5,
            name="frequency_dark",
        )

    def _mm_to_s(self, mm):
        return mm * 1e-3 * self._passes / self._group_velo * self._direction

    def _s_to_mm(self, s):
        return s * self._group_velo * 1e3 / self._passes * self._direction

    def __repr__(self):
        s = ""
        s += f"{colorama.Style.DIM}"
        s += datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.RESET_ALL}"
        s += f"{colorama.Style.BRIGHT}{self._get_name()}{colorama.Style.RESET_ALL} at "
        s += f"{(self.get_current_value()*ureg.second).to_compact():P~6.3f}"
        s += f"{colorama.Style.RESET_ALL}"
        return s

    def get_limits(self):
        return [self._mm_to_s(tl) for tl in self._stage.get_limits()]

    def set_limits(self, low_limit, high_limit):
        lims_stage = [self._s_to_mm(tl) for tl in [low_limit, high_limit]]
        lims_stage.sort()
        self._stage.set_limits(*lims_stage)

        return [self._mm_to_s(tl) for tl in self._stage.get_limits()]


class DelayCompensation(AdjustableVirtual):
    """Simple virtual adjustable for compensating delay adjustables. It assumes the first adjustable is the master for
    getting the current value."""

    def __init__(self, adjustables, directions, set_current_value=True, name=None):
        self._directions = directions
        self.Id = name
        AdjustableVirtual.__init__(
            self,
            adjustables,
            self._from_values,
            self._calc_values,
            set_current_value=set_current_value,
            name=name,
        )

    def _calc_values(self, value):
        return tuple(tdir * value for tdir in self._directions)

    def _from_values(self, *args):
        positions = [ta * tdir for ta, tdir in zip(args, self._directions)]
        return positions[0]

        tuple(tdir * value for tdir in self._directions)

    def __repr__(self):
        s = ""
        s += f"{colorama.Style.DIM}"
        s += datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S") + ": "
        s += f"{colorama.Style.RESET_ALL}"
        s += f"{colorama.Style.BRIGHT}{self._get_name()}{colorama.Style.RESET_ALL} at "
        s += f"{(self.get_current_value()*ureg.second).to_compact():P~6.3f}"
        s += f"{colorama.Style.RESET_ALL}"
        return s


class Laser_Exp:
    def __init__(self, Id=None, name=None, smar_config=None):
        self.Id = Id
        self.IdExp1 = "SARES20-EXP"
        self.IdSA = "SARES23"
        self.name = name
        self.alias = Alias(name)
        self.smar_config = smar_config

        # Waveplate and Delay stage
        try:
            addMotorRecordToSelf(self, self.Id + "-M534:MOT", name="wp_eos")
            addMotorRecordToSelf(self, self.Id + "-M533:MOT", name="wp_bsen")
        except:
            print("No wp found")

        try:
            addMotorRecordToSelf(
                self, Id=self.Id + "-M521:MOTOR_1", name="_delay_eos_stg"
            )
            # addDelayStageToSelf(
            #    self, stage=self.__dict__["_delay_eos_stg"], name="delay_eos"
            # )
            self.delay_eos = DelayTime(self._delay_eos_stg, name="delay_eos")
            self.alias.append(self.delay_eos.alias)
        except Exception as expt:
            print("No EOS delay stage")
            print(expt)

        addMotorRecordToSelf(self, Id=self.Id + "-M524:MOTOR_1", name="_delay_bsen_stg")
        addMotorRecordToSelf(self, Id="SARES20-MF1:MOT_5", name="par_y")
        self.delay_bsen = DelayTime(self._delay_bsen_stg, name="delay_bsen")
        self.alias.append(self.delay_bsen.alias)

        try:
            addMotorRecordToSelf(
                self, Id=self.Id + "-M522:MOTOR_1", name="_delay_thz_stg"
            )
            self.delay_thz = DelayTime(self._delay_thz_stg, name="delay_thz")
            self.alias.append(self.delay_thz.alias)
        except:
            print("Problems initializing global delay stage")
        try:
            addMotorRecordToSelf(
                self, Id=self.Id + "-M523:MOTOR_1", name="_delay_glob_stg"
            )
            self.delay_glob = DelayTime(self._delay_glob_stg, name="delay_glob")
            self.alias.append(self.delay_glob.alias)
        except:
            print("Problems initializing global delay stage")

        # Implementation of delay compensation, this assumes for now that delays_glob and delay_tt actually delay in positive directions.
        # try:
        #    self.delay_lxtt = DelayCompensation(
        #        [self.delay_glob, self.delay_tt], [-1, 1], name="delay_lxtt"
        #    )
        #    self.alias.append(self.delay_lxtt.alias)
        # except:
        #    print("Problems initializing virtual pump delay stage")
        # compressor
        addMotorRecordToSelf(self, Id=self.Id + "-M532:MOT", name="compressor")
        # self.compressor = MotorRecord(Id+'-M532:MOT')
        # LAM delay stages
        # addSmarActRecordToSelf(self, Id="SLAAR21-LMTS-LAM11", name="_lam_delay_smarstg")
        # addDelayStageToSelf(self, self.__dict__["_lam_delay_smarstg"], name="lam_delay_smar")
        # self._lam_delayStg_Smar = SmarActRecord('SLAAR21-LMTS-LAM11')
        # self.lam_delay_Smar = DelayStage(self._lam_delayStg_Smar)
        # try:
        #    addMotorRecordToSelf(self, Id=self.Id + "-M548:MOT", name="_lam_delaystg")
        #    addDelayStageToSelf(
        #        self, self.__dict__["_lam_delaystg"], name="lam_delay"
        #    )  # this try except does not work
        # except:
        #    print("Problems initializing LAM delay stage")
        # self._lam_delayStg = MotorRecord(self.Id+'-M548:MOT')
        # self.lam_delay = DelayStage(self._lam_delayStg)

        # PALM delay stages
        # addMotorRecordToSelf(self, Id=self.Id + "-M552:MOT", name="_palm_delaystg")
        # addDelayStageToSelf(self, self.__dict__["_palm_delaystg"], name="palm_delay")
        # self._palm_delayStg = MotorRecord(self.Id+'-M552:MOT')
        # self.palm_delay = DelayStage(self._palm_delayStg)

        # PSEN delay stages
        # self._psen_delayStg = MotorRecord(self.Id+'')
        # self.psen_delay = DelayStage(self._pump_delayStg)
        try:
            addMotorRecordToSelf(self, Id=self.Id + "-M561:MOT", name="_psen_delaystg")
            addDelayStageToSelf(
                self, stage=self.__dict__["_psen_delaystg"], name="psen_delay"
            )
        except Exception as expt:
            print("No psen delay stage")
            print(expt)

        ### SmarAct stages used in the experiment ###
        try:
            for name, config in self.smar_config.items():
                addSmarActRecordToSelf(self, Id=self.IdSA + config["id"], name=name)
        except Exception as expt:
            print("Issue with initializing smaract stages from eco smar_config")
            print(expt)

    def set_stage_config(self):
        for name, config in self.smar_config.items():
            mot = self.__dict__[name]
            mot.caqtdm_name.mv(config["pv_descr"])
            mot.stage_type.mv(config["type"])
            mot.sensor_type.mv(config["sensor"])
            mot.speed.mv(config["speed"])
            if "direction" in config.keys():
                mot.direction.mv(config["direction"])
            sleep(0.5)
            mot.calibrate_sensor.mv(1)

    def home_smaract_stages(self, stages=None):
        if stages == None:
            stages = self.smar_config.keys()
        print("#### Positions before homing ####")
        print(self.__repr__())
        for name in stages:
            config = self.smar_config[name]
            mot = self.__dict__[name]
            print(
                "#### Homing {} in {} direction ####".format(
                    name, config["home_direction"]
                )
            )
            if config["home_direction"] == "back":
                mot.home_backward.mv(1)
            elif config["home_direction"] == "forward":
                mot.home_forward.mv(1)

        ## IR beam pointing mirrors
        # try:
        #    addPvRecordToSelf(self, pvsetname="SLAAR21-LMNP-ESBIR13:DRIVE", pvreadbackname ="SLAAR21-LMNP-ESBIR13:MOTRBV", accuracy= 10, name='IR_mirr1_ry')
        #    addPvRecordToSelf(self, pvsetname="SLAAR21-LMNP-ESBIR14:DRIVE", pvreadbackname ="SLAAR21-LMNP-ESBIR14:MOTRBV", accuracy= 10, name='IR_mirr1_rx')
        # except:
        #    print("Issue intializing picomotor IR beam pointing mirrors")
        #    pass
        try:
            addSmarActRecordToSelf(self, Id="SARES23-ESB4", name="IR_mirr1_rx")
            addSmarActRecordToSelf(self, Id="SARES23-LIC7", name="IR_mirr1_ry")

            addSmarActRecordToSelf(self, Id="SARES23-ESB1", name="IR_mirr2_ry")
            addSmarActRecordToSelf(self, Id="SARES23-ESB2", name="IR_mirr2_rz")
            addSmarActRecordToSelf(self, Id="SARES23-ESB3", name="IR_mirr2_z")
        except:
            print("Issue intializing SmarAct IR beam pointing mirrors")
            pass

        ## beam pointing offsets
        try:

            def set_position_monitor_offsets(
                cam1_center=[None, None], cam2_center=[None, None]
            ):
                dims = ["x", "y"]
                channels_cam1_xy = [
                    "SLAAR21-LTIM01-EVR0:CALCS.INPB",
                    "SARES20-CVME-01-EVR0:CALCI.INPB",
                ]
                channels_cam2_xy = [
                    "SARES20-CVME-01-EVR0:CALCX.INPB",
                    "SARES20-CVME-01-EVR0:CALCY.INPB",
                ]
                print("Old crosshair position cam1")
                for dim, tc, tv in zip(dims, channels_cam1_xy, cam1_center):
                    print(f"{dim}: {PV(tc).get()}")
                    # PV(tc).put(bytes(str(tv), "utf8"))
                print("Old crosshair position cam2")
                for dim, tc, tv in zip(dims, channels_cam2_xy, cam2_center):
                    print(f"{dim}: {PV(tc).get()}")
                    # PV(tc).put(bytes(str(tv), "utf8"))
                print("New crosshair position cam1")
                for dim, tc, tv in zip(dims, channels_cam1_xy, cam1_center):
                    if not tv:
                        break
                    print(f"{dim}: {tv}")
                    PV(tc).put(bytes(str(tv), "utf8"))
                print("New crosshair position cam2")
                for dim, tc, tv in zip(dims, channels_cam2_xy, cam2_center):
                    if not tv:
                        break
                    print(f"{dim}: {tv}")
                    PV(tc).put(bytes(str(tv), "utf8"))

            self.set_position_monitor_offsets = set_position_monitor_offsets
        except:
            pass

    def get_adjustable_positions_str(self):
        ostr = "*****Laser motor positions******\n"

        for tkey, item in sorted(self.__dict__.items()):
            if hasattr(item, "get_current_value"):
                pos = item.get_current_value()
                posdialstr = ""
                try:
                    posdial = item.get_current_value(postype="dial")
                    posdialstr = "    dial:  % 14g\n" % posdial
                except:
                    pass
                ostr += "  " + tkey.ljust(18) + " : % 14g\n" % pos + posdialstr
        return ostr

    def __repr__(self):
        return self.get_adjustable_positions_str()


