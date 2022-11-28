from ..devices_general.motors import MotorRecord

# from ..devices_general.smaract import SmarActRecord
# from epics import PV
from ..devices_general.delay_stage import DelayStage

# from ..devices_general.adjustable import
from ..aliases import Alias, append_object_to_object
from ..loptics.bernina_experiment import DelayTime
from cam_server import PipelineClient
from eco import Assembly


class TargetStages(Assembly):
    def __init__(self,*args,name='target'):
        super().__init__(name=name)
        for df in args:
            self._append(MotorRecord,df[1],name=df[0], is_display=True, is_setting=True)


class SpectralEncoder(Assembly):
    def __init__(
        self,
        pvname,
        name=None,
        reduction_client_address="http://sf-daqsync-01:8889/",
        delay_stages={
            "spect_tt": "SLAAR21-LMOT-M553:MOT",
            "retroreflector": "SLAAR21-LMOT-M561:MOT",
        },
        mirror_stages=None,
    ):
        super().__init__(name=name)
        self.pvname = pvname

        self._append(
            TargetStages,
            ('x',pvname + ":MOTOR_X1"), 
            ('y',pvname + ":MOTOR_Y1"), 
            name='target_stages', 
            is_display='recursive', 
            is_setting=True,
            )
        
        if delay_stages:
            for key, pv in delay_stages.items():
                tname = "delay_" + key + "_stg"
                self._append(MotorRecord, pv, name=tname, is_setting=True)
                self._append(
                    DelayTime, self.__dict__[tname], name="delay_" + key
                )  
                
        if mirror_stages is not None:
            for key, pv in mirror_stages.items():
                self._append(MotorRecord, pv, name=key, is_setting=True)

    # @property
    # def roi(self):
    # return self.data_reduction_client.get_roi_signal()

    # @roi.setter
    # def roi(self, values):tt_opt
    # self.data_reduction_client.set_roi_signal(values)

    # @property
    # def roi_background(self):
    # return self.data_reduction_client.get_roi_background()

    # @roi_background.setter
    # def roi_background(self, values):
    # self.data_reduction_client.set_roi_background(values)


class SpatialEncoder:
    def __init__(
        self,
        name=None,
        reduction_client_address="http://sf-daqsync-02:12003/",
        delay_stages={"spatial_tt": "SLAAR21-LMOT-M522:MOTOR_1"},
        pipeline_id="SARES20-CAMS142-M4_psen_db",
    ):
        self.name = name
        self.alias = Alias(name)
        # append_object_to_object(self,MotorRecord,pvname+":MOTOR_X1",name='x_target')
        # append_object_to_object(self,MotorRecord,pvname+":MOTOR_Y1",name='y_target')
        if delay_stages:
            for key, pv in delay_stages.items():
                tname = "delay_" + key + "_stg"
                append_object_to_object(self, MotorRecord, pv, name=tname)
                append_object_to_object(
                    self, DelayTime, self.__dict__[tname], name="delay_" + key
                )

        # self.delay = MotorRecord(self.Id + "-M424:MOT")
        # self.delayTime = DelayStage(self.delay)
        # self.data_reduction_client =  PsenProcessingClient(address=reduction_client_address)
        self._camera_server_client = PipelineClient()
        self._camera_server_pipeline_id = pipeline_id

    # @property
    # def roi(self):
    # return self.data_reduction_client.get_roi_signal()
    # @roi.setter
    # def roi(self,values):
    # self.data_reduction_client.set_roi_signal(values)

    # @property
    # def roi_background(self):
    # return self.data_reduction_client.get_roi_background()
    # @roi_background.setter
    # def roi_background(self,values):
    # self.data_reduction_client.set_roi_background(values)

    @property
    def roi(self):
        return self._camera_server_client.get_instance_config(
            self._camera_server_pipeline_id
        )["roi_signal"]

    @roi.setter
    def roi(self, values):
        self._camera_server_client.set_instance_config(
            self._camera_server_pipeline_id, {"roi_signal": values}
        )

    @property
    def roi_background(self):
        return self._camera_server_client.get_instance_config(
            self._camera_server_pipeline_id
        )["roi_background"]

    @roi_background.setter
    def roi_background(self, values):
        self._camera_server_client.set_instance_config(
            self._camera_server_pipeline_id, {"roi_background": values}
        )

    def __repr__(self):
        s = [f"Status {self.name}"]
        # s.append(str(self.x_target))
        # s.append(str(self.y_target))
        s.append(f"Data reduction is on")
        s.append(f"  roi            {self.roi}")
        s.append(f"  roi_background {self.roi_background}")
        return "\n".join(s)
