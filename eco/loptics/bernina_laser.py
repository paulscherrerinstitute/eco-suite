from ..elements.assembly import Assembly
from ..devices_general.motors import SmaractStreamdevice, MotorRecord


class LaserBernina(Assembly):
    def __init__(self,pvname,name=name):
        super().__init__(name=name)
        self._append()
        self.pvname = pvname
        # Table in bernina hutch
        # Waveplate and Delay stage
        self._append(MotorRecord, self.pvname+"-M534:MOT", name="wp_eos",is_setting=True)
        self._append(MotorRecord, self.pvname+"-M533:MOT", name="wp_bsen",is_setting=True)
        self._append(MotorRecord, self.pvname+"-M521:MOTOR_1", name="delay_eos_stage",is_setting=True)
        
        # todo
        self.delay_eos = DelayTime(self._delay_eos_stg, name="delay_eos")
        
        addMotorRecordToSelf(self, Id=self.Id + "-M524:MOTOR_1", name="_delay_bsen_stg")
        addMotorRecordToSelf(self, Id="SARES20-MF1:MOT_5", name="par_y")
        self.delay_bsen = DelayTime(self._delay_bsen_stg, name="delay_bsen")

            addMotorRecordToSelf(
                self, Id=self.Id + "-M522:MOTOR_1", name="_delay_thz_stg"
            )
            self.delay_thz = DelayTime(self._delay_thz_stg, name="delay_thz")
            addMotorRecordToSelf(
                self, Id=self.Id + "-M523:MOTOR_1", name="_delay_glob_stg"
            )
            self.delay_glob = DelayTime(self._delay_glob_stg, name="delay_glob")

        # compressor
        addMotorRecordToSelf(self, Id=self.Id + "-M532:MOT", name="compressor")
        
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
