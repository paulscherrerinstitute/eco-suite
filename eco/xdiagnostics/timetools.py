from ..devices_general.motors import MotorRecord
# from ..devices_general.smaract import SmarActRecord
# from epics import PV
from ..devices_general.delay_stage import DelayStage
# from ..devices_general.adjustable import 
from ..aliases import Alias,append_object_to_object
from psen_processing import PsenProcessingClient
from ..loptics.bernina_experiment import DelayTime


class SpectralEncoder:
    def __init__(self, pvname, name=None, reduction_client_address="http://sf-daqsync-02:12002/",delay_stages={'spect_tt':"SLAAR21-LMOT-M553:MOT","retroreflector":"SLAAR21-LMOT-M561:MOT"}):
        self.pvname = pvname
        self.name=name
        self.alias = Alias(name)
        append_object_to_object(self,MotorRecord,pvname+":MOTOR_X1",name='x_target')
        append_object_to_object(self,MotorRecord,pvname+":MOTOR_Y1",name='y_target')
        if delay_stages:
            for key,pv in delay_stages.items():
                tname = 'delay_'+key+'_stg'
                append_object_to_object(self,MotorRecord,pv,name=tname)
                append_object_to_object(self,DelayTime,self.__dict__[tname],name='delay_'+key)

        # self.delay = MotorRecord(self.Id + "-M424:MOT")
        # self.delayTime = DelayStage(self.delay)
        self.data_reduction_client =  PsenProcessingClient(address=reduction_client_address)

    
    @property
    def roi(self):
        return self.data_reduction_client.get_roi_signal()
    @roi.setter
    def roi(self,values):
        self.data_reduction_client.set_roi_signal(values)
    
    @property
    def roi_background(self):
        return self.data_reduction_client.get_roi_background()
    @roi_background.setter
    def roi_background(self,values):
        self.data_reduction_client.set_roi_background(values)

    def __repr__(self):
        s = [f"Status {self.name}"]
        s.append(str(self.x_target))
        s.append(str(self.y_target))
        s.append(f"Data reduction is {self.data_reduction_client.get_status()}")
        s.append(f"  roi            {self.roi}")
        s.append(f"  roi_background {self.roi_background}")
        return '\n'.join(s)

