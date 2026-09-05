from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent



namespace.append_obj(
    "laser_shutter",
    "SLAAR21-LTIM01-EVR0",
    name="laser_shutter",
    module_name="eco.loptics.laser_shutter",
    lazy=True,
)

namespace.append_obj(
    "LaserBernina",
    lazy=True,
    name="las",
    module_name="eco.loptics.bernina_laser",
    pvname="SLAAR21-LMOT",
)
namespace.append_obj(
    "PositionMonitors",
    lazy=True,
    name="las_pointing_monitors",
    module_name="eco.loptics.bernina_laser",
)

namespace.append_obj(
    "LxtCompStageDelay",
    NamespaceComponent(namespace, "tt_kb.delay"),
    NamespaceComponent(namespace, "las.xlt"),
    feedback_enabled_adj=NamespaceComponent(namespace, "tt_kb.feedback_enabled"),
    lazy=True,
    name="lxt",
    module_name="eco.loptics.bernina_laser",
)


namespace.append_obj(
    "IncouplingCleanBernina",
    lazy=True,
    name="clic",
    module_name="eco.loptics.bernina_laser",
)

# namespace.append_obj(
#     "MidIR",
#     lazy=True,
#     name="midir",
#     module_name="eco.loptics.bernina_laser",
# )


namespace.append_obj(
    "OPAHE_bernina",
    name="opa_he",
    lazy=True,
    module_name="eco.loptics.opa",
)


namespace.append_obj(
    "Incoupling",
    delaystage_pump=NamespaceComponent(namespace, "las.delaystage_pump"),
    lazy=True,
    name="las_inc",
    module_name="eco.endstations.bernina_incoupling",
)


namespace.append_obj(
    "THzWork",
    # delaystage_pump=NamespaceComponent(namespace, "las.delaystage_pump"),
    lazy=True,
    name="thz_dev",
    module_name="eco.loptics.bernina_laser",
)

# namespace.append_obj(
#    "Organic_crystal_breadboard",
#    lazy=True,
#    name="ocb",
#    delay_offset_detector=NamespaceComponent(namespace, "thc.delay_x_center"),
#    thc_x_adjustable=NamespaceComponent(namespace, "thc.x"),
#    module_name="eco.endstations.bernina_sample_environments",
# )





# namespace.append_obj(
#     "Organic_crystal_breadboard",
#     lazy=True,
#     name="ocb",
#     delay_offset_detector=None,
#     thc_x_adjustable=None,
#     module_name="eco.endstations.bernina_sample_environments",
# )

# namespace.append_obj(
#     "Electro_optic_sampling",
#     lazy=True,
#     name="eos",
#     module_name="eco.endstations.bernina_sample_environments",
# )
# namespace.append_obj(
#     "Electro_optic_sampling_new",
#     lazy=True,
#     name="eos_new",
#     module_name="eco.endstations.bernina_sample_environments",
# )
# class Sample_stages(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(MotorRecord, "SARES20-MF1:MOT_11", name="x", is_setting=True)
#         self._append(MotorRecord, "SARES20-MF1:MOT_9", name="y", is_setting=True)

# namespace.append_obj(
#    Sample_stages,
#    lazy=True,
#    name="sample",
# )

# class LaserSteering(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(SmaractRecord, "SARES23-USR:MOT_3", name="mirr1_pitch", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_4", name="mirr1_roll", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_14", name="mirr2_pitch", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_12", name="mirr2_roll", is_setting=True)

# class THzGeneration(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_16", name="par_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_8", name="mirr_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_7", name="mirr_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_18", name="mirr_ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_9", name="mirr_rz", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_15", name="polarizer", is_setting=True)


# class THzVirtualStages(Assembly):
#     def __init__(self, name=None, mx=None, mz=None, px=None, pz=None):
#         super().__init__(name=name)
#         self._mx = mx
#         self._mz = mz
#         self._px = px
#         self._pz = pz
#         self._append(
#             AdjustableFS,
#             "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/p21145_mirr_x0.json",
#             name="offset_mirr_x",
#             default_value=0,
#             is_setting=True,
#         )
#         self._append(
#             AdjustableFS,
#             "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/p21145_mirr_z0.json",
#             name="offset_mirr_z",
#             default_value=0,
#             is_setting=True,
#         )
#         self._append(
#             AdjustableFS,
#             "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/p21145_par_x0.json",
#             name="offset_par_x",
#             default_value=0,
#             is_setting=True,
#         )
#         self._append(
#             AdjustableFS,
#             "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/p21145_par_z0.json",
#             name="offset_par_z",
#             default_value=0,
#             is_setting=True,
#         )

#         def get_divergence(mx, px):
#             return px - self.offset_par_x()

#         def set_divergence(x):
#             mx = self.offset_mirr_x() + x
#             px = self.offset_par_x() + x
#             return mx, px

#         def get_focus_z(mx, pz):
#             return pz - self.offset_par_z()

#         def set_focus_z(z):
#             mz = self.offset_mirr_z() + z
#             pz = self.offset_par_z() + z
#             return mz, pz

#         self._append(
#             AdjustableVirtual,
#             [mx, px],
#             get_divergence,
#             set_divergence,
#             name="divergence_virtual",
#         )
#         self._append(
#             AdjustableVirtual,
#             [mz, pz],
#             get_focus_z,
#             set_focus_z,
#             name="focus_virtual",
#         )

#     def set_offsets_to_current_value(self):
#         self.offset_mirr_x.mv(self._mx())
#         self.offset_mirr_z.mv(self._mz())
#         self.offset_par_x.mv(self._px())
#         self.offset_par_z.mv(self._pz())


# class THz(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)
#         self._append(SmaractRecord, "SARES23-USR:MOT_6", name="par_x", is_setting=True)
#         self._append(MotorRecord, "SARES20-MF1:MOT_10", name="par_y", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_13", name="par_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_14", name="par_rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_15", name="par_ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_1", name="delaystage_thz", is_setting=True,)
#         self._append(DelayTime, self.delaystage_thz, name="delay_thz", is_setting=False, is_display=True,)
#         self._append(LaserSteering, name="ir_pointing", is_setting=False)
#         self._append(THzGeneration, name="generation", is_setting=False)

### Virtual stages ###
# self._append(
#     THzVirtualStages,
#     name="virtual_stages",
#     mx=self.generation.mirr_x,
#     mz=self.generation.mirr_z,
#     px=self.generation.par_x,
#     pz = self.par_z,
#     is_setting=False)


# namespace.append_obj(
#    THz,
#    lazy=True,
#    name="thz",
#         self._append(
#             AdjustableVirtual,
#             [self.crystal_ROT, self.thz_wp],
#             self.thz_pol_get,
#             self.thz_pol_set,
#             name="",
#         )
#         # self.thz_polarization = AdjustableVirtual(
#         #     [self.crystal_ROT, self.thz_wp],
#         #     self.thz_pol_get,
#         #     self.thz_pol_set,
#         #     name="thz_polarization",
#         # )
#         self._append(
#             AdjustableVirtual,
#             [self.delay_thz, self.delay_800_pump],
#             self.delay_get,
#             self.delay_set,
#             name="combined_delay",
#         )

#         # self.combined_delay = AdjustableVirtual(
#         #     [self.delay_thz, self.delay_800_pump],
#         #     self.delay_get,
#         #     self.delay_set,
#         #     name="combined_delay",
#         # )

#     def thz_pol_set(self, val):
#         return 1.0 * val, 1.0 / 2 * val

#     def thz_pol_get(self, val, val2):
#         return 1.0 * val2
# )

# class THz_in_air(Assembly):
#     def __init__(self, name=None):
#         super().__init__(name=name)

#         self._append(SmaractRecord, "SARES23-USR:MOT_5", name="crystal_ROT", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_15", name="ir_1_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_13", name="ir_1_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-LIC:MOT_14", name="ir_1_Rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_10", name="ir_2_Rx", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_7", name="ir_2_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_9", name="para_2_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_3", name="thz_mir_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_1", name="thz_mir_z", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_8", name="thz_mir_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_2", name="thz_mir_Rz", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_6", name="focus_z", is_setting=True)
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_4",
#             name="focus_y",
#             is_setting=True,
#             is_display=True,
#         )
#         self._append(SmaractRecord, "SARES23-USR:MOT_14", name="focus_x", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_13", name="focus_Rz", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_15", name="focus_Ry", is_setting=True)
#         self._append(SmaractRecord, "SARES23-USR:MOT_11", name="focus_Rx", is_setting=True)
#         self._append(SmaractRecord, ":MOT_18", name="thz_wp", is_setting=True)
#         self._append(
#             SmaractRecord, "SARES23-LIC:MOT_16", name="delaystage_thz", is_setting=True
#         )
#         self._append(DelayTime, self.delaystage_thz, name="delay_thz", is_setting=True)
#         self._append(
#             MotorRecord,
#             "SLAAR21-LMOT-M521:MOTOR_1",
#             name="delaystage_800_pump",
#             is_setting=True,
#         )
#         self._append(
#             DelayTime, self.delaystage_800_pump, name="delay_800_pump", is_setting=True
#         )
#         self._append(
#             AdjustableFS,
#             "/sf/bernina/code/gac-bernina/eco_cnf_bernina/configuration/combined_delta.json",
#             name="combined_delta",
#             default_value=0,
#             is_setting=True,
#         )
#         self.delay_thz = DelayTime(self.delaystage_thz, name="delay_thz")

#         self._append(
#             AdjustableVirtual,
#             [self.crystal_ROT, self.thz_wp],
#             self.thz_pol_get,
#             self.thz_pol_set,
#             name="",
#         )
#         # self.thz_polarization = AdjustableVirtual(
#         #     [self.crystal_ROT, self.thz_wp],
#         #     self.thz_pol_get,
#         #     self.thz_pol_set,
#         #     name="thz_polarization",
#         # )
#         self._append(
#             AdjustableVirtual,
#             [self.delay_thz, self.delay_800_pump],
#             self.delay_get,
#             self.delay_set,
#             name="combined_delay",
#         )

#         # self.combined_delay = AdjustableVirtual(
#         #     [self.delay_thz, self.delay_800_pump],
#         #     self.delay_get,
#         #     self.delay_set,
#         #     name="combined_delay",
#         # )

#     def thz_pol_set(self, val):
#         return 1.0 * val, 1.0 / 2 * val

#     def thz_pol_get(self, val, val2):
#         return 1.0 * val2

#     def delay_set(self, val):
#         return 1.0 * val + self.combined_delta(), 1.0 * val

#     def delay_get(self, val, val2):
#         return 1.0 * val2


# namespace.append_obj(
#    THz_in_air,
#    lazy=True,
#    name="thz",
# )




# class JohannAnalyzer(Assembly):
#     def __init__(self, name=""):
#         super().__init__(name=name)
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_3",
#             name="pitch",
#             is_setting=True,
#             is_display=True,
#         )
#         self._append(
#             MotorRecord,
#             "SARES20-MF1:MOT_4",
#             name="roll",
#             is_setting=True,
#             is_display=True,
#         )


# namespace.append_obj(JohannAnalyzer, name="analyzer", lazy=True)


# class GratingHolder(Assembly):
#     def __init__(self, name=""):
#         super().__init__(name=name)
#         self._append(y=True,
#             MotorRecord,
#             "SARES20-MF1:MOT_7",
#             name="vertical",
#             is_setting=True,
#             is_display=True,
#         )
#         self._append(
#             SmaractRecord,
#             "SARES23-USR:MOT_6",
#             name="horizontal",
#             is_setting=True,
#             is_display=True,
#         )


# namespace.append_obj(GratingHolder, name="grating_holder")


# ad hoc 2 pulse setup
# class Laser2pulse(Assembly):
#    def __init__(self, name=None):y=True,
#        super().__init__(name=name)
#        self._append(
#            SmaractStreamdevice,
#            "SARES23-ESB1",
#            name="pump_exp_delaystage",
#            is_setting=True,
#        )
#
#        self._append(
#            DelayTime,
#            self.pump_exp_delaystage,
#            name="pump_delay_exp",
#            is_setting=False,
#            is_display=True,
#            reset_current_value_to=False,
#        )
#        self._append(SmaractStreamdevice, "SARES23-ESB5", name="wp", is_setting=True)
#        self._append(
#            SmaractStreamdevice,
#            "SARES23-ESB4",
#            name="pump_2_delaystage",
#            is_setting=True,
#        )
#        self._append(
#            DelayTime,
#            self.pump_2_delaystage,
#            name="pump_2_delay",
#            is_setting=False,
#            is_display=True,
#            reset_current_value_to=False,
#        )
#        self._append(SmaractStreamdevice, "SARES23-ESB6", name="ratio", is_setting=True)
#        self._append(
#            SmaractStreamdevice, "SARES23-ESB17", name="rx_pump", is_setting=True
#        )
#        self._append(
#            SmaractStreamdevice, "SARES23-ESB18", name="ry_pump", is_setting=True
#        )
#
#
# namespace.append_obj(
#    Laser2pulse,
#    lazy=True,
#    name="laser2pulse",
# )


# from eco.xoptics import dcm_pathlength_compensation as dpc

# namespace._append(
#     "MotorRecord",
#     "SLAAR21-LMOT-M523:MOTOR_1",
#     name="delaystage_glob",
#     is_setting=True,
#     module_name="eco.devices_general.motors",
# )
# namespace.append(
#     "DelayTime",
#     delaystage_glob,
#     name="delay_glob",
#     is_setting=True,
#     module_name="eco.loptics.bernina_laser",
# )
