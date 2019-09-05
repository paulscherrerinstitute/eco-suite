# # New configuration of components:
# components is an ordered list of
# - name in parent package
# - type, describing the python Class or factory function.
# - arguments of that type args
# - kwargs of that type

# # Conventions for the type
# the call of type will try to pass a kwarg 'name' with the
# name of the component, before only calling args and kwargs.
# if arg or kwarg is of type eco.utilities.Component (dummy class)
# this indicates that an earlier initialized object is used
# (e.g. from same configuration).
from ..utilities.config import (
    Component,
    Alias,
    init_device,
    initFromConfigList,
    Configuration,
)

_eco_lazy_init = False

config = Configuration(
    "/sf/bernina/config/eco/bernina_config_eco.json", name="bernina_config"
)

components = [
    #        {
    #            'name'  : 'device_alias_name',
    #            'type'  : 'package.module.submodule:ClassOrFactory',
    #            'args'  : ['all','the','requires','args'],
    #            'kwargs': {}
    #            }
    {
        "type": "eco.utilities.config:append_to_path",
        "args": config["path_exp"],
        "name": "path_exp",
        "kwargs": {},
        "lazy": False,
    },
    {
        "name": "elog",
        "type": "eco.utilities.elog:Elog",
        "args": ["https://elog-gfa.psi.ch/Bernina"],
        "kwargs": {
            "user": "gac-bernina",
            "screenshot_directory": "/sf/bernina/config/screenshots",
        },
    },
    {
        "name": "screenshot",
        "type": "eco.utilities.elog:Screenshot",
        "args": [],
        "kwargs": {"screenshot_directory": "/sf/bernina/config/screenshots"},
    },
    {
        "name": "slit_und",
        "type": "eco.xoptics.slits:SlitFourBlades_old",
        "args": ["SARFE10-OAPU044"],
        "kwargs": {},
        "desc": "Slit after Undulator",
    },
    {
        "name": "att_fe",
        "type": "eco.xoptics.attenuator_aramis:AttenuatorAramis",
        "args": ["SARFE10-OATT053"],
        "kwargs": {},
        "z_und": 53,
        "desc": "Attenuator in Front End",
    },
    {
        "name": "mon_und",
        "z_und": 53,
        "desc": "Intensity/position monitor after Optics hutch",
        "type": "eco.xdiagnostics.intensity_monitors:SolidTargetDetectorPBPS",
        "args": ["SARFE10-PBPS053"],
        "kwargs": {},
    },
    {
        "name": "prof_fe",
        "args": ["SARFE10-PPRM064"],
        "kwargs": {},
        "z_und": 64,
        "desc": "Profile monitor after Front End",
        "type": "eco.xdiagnostics.profile_monitors:Pprm",
    },
    {
        "name": "prof_mirr_alv1",
        "args": ["SAROP11-PPRM066"],
        "kwargs": {},
        "z_und": 66,
        "desc": "Profile monitor after Alvra Mirror 1",
        "type": "eco.xdiagnostics.profile_monitors:Pprm",
    },
    # {
        # "name": "slitSwitch",
        # "z_und": 92,
        # "desc": "Slit in Optics hutch after Photon switchyard and before Bernina optics",
        # "type": "eco.xoptics.slits:SlitBlades_old",
        # "args": ["SAROP21-OAPU092"],
        # "kwargs": {},
    # },
    {
        "name": "slit_switch",
        "z_und": 92,
        "desc": "Slit in Optics hutch after Photon switchyard and before Bernina optics",
        "type": "eco.xoptics.slits:SlitBlades",
        "args": ["SAROP21-OAPU092"],
        "kwargs": {},
    },
    {
        "name": "prof_mirr1",
        "args": ["SAROP21-PPRM094"],
        "kwargs": {},
        "z_und": 94,
        "desc": "Profile monitor after Mirror 1",
        "type": "eco.xdiagnostics.profile_monitors:Pprm",
    },
    {
        "name": "mirr1",
        "args": [],
        "kwargs": {},
        "z_und": 92,
        "desc": "Vertical offset mirror 1",
        "type": "eco.xoptics.offsetMirrors:OffsetMirror",
        "kwargs": {"Id": "SAROP21-OOMV092"},
    },
    {
        "name": "mirr2",
        "args": [],
        "kwargs": {},
        "z_und": 96,
        "desc": "Vertical offset mirror 2",
        "type": "eco.xoptics.offsetMirrors:OffsetMirror",
        "kwargs": {"Id": "SAROP21-OOMV096"},
    },
    {
        "name": "mono",
        "args": ["SAROP21-ODCM098"],
        "kwargs": {},
        "z_und": 98,
        "desc": "DCM Monochromator",
        "type": "eco.xoptics.dcm:Double_Crystal_Mono",
    },
    {
        "name": "prof_mono",
        "args": ["SAROP21-PPRM102"],
        "kwargs": {},
        "z_und": 102,
        "desc": "Profile monitor after Monochromator",
        "type": "eco.xdiagnostics.profile_monitors:Pprm",
    },
    {
        "name": "xp",
        "args": [],
        "kwargs": {
            "Id": "SAROP21-OPPI103",
            "evronoff": "SGE-CPCW-72-EVR0:FrontUnivOut15-Ena-SP",
            "evrsrc": "SGE-CPCW-72-EVR0:FrontUnivOut15-Src-SP",
        },
        "z_und": 103,
        "desc": "X-ray pulse picker",
        "type": "eco.xoptics.pp:Pulsepick",
        "lazy": False,
    },
    {
        "name": "mon_opt_old",
        "z_und": 133,
        "desc": "Intensity/position monitor after Optics hutch",
        "type": "eco.xdiagnostics.intensity_monitors:SolidTargetDetectorPBPS",
        "args": ["SAROP21-PBPS133"],
        "kwargs": {"VME_crate": "SAROP21-CVME-PBPS1", "link": 9},
    },
    {
        "name": "mon_opt",
        "z_und": 133,
        "desc": "Intensity/position monitor after Optics hutch",
        "type": "eco.xdiagnostics.intensity_monitors:SolidTargetDetectorPBPS_new",
        "args": ["SAROP21-PBPS133"],
        "kwargs": {"VME_crate": "SAROP21-CVME-PBPS1", "link": 9, 'channels':{'up':'SLAAR21-LSCP1-FNS:CH4:VAL_GET','down':'SLAAR21-LSCP1-FNS:CH5:VAL_GET','left':'SLAAR21-LSCP1-FNS:CH6:VAL_GET','right':'SLAAR21-LSCP1-FNS:CH7:VAL_GET'},'calc':{'itot':'SLAAR21-LTIM01-EVR0:CALCI','xpos':'SLAAR21-LTIM01-EVR0:CALCX','ypos':'SLAAR21-LTIM01-EVR0:CALCY'}},
    },
    {
        "name": "prof_opt",
        "args": ["SAROP21-PPRM133"],
        "kwargs": {},
        "z_und": 133,
        "desc": "Profile monitor after Optics hutch",
        "type": "eco.xdiagnostics.profile_monitors:Pprm",
    },
    {
        "name": "att",
        "args": ["SAROP21-OATT135"],
        "kwargs": {'pulse_picker':Component('xp')},
        "z_und": 135,
        "desc": "Attenuator Bernina",
        "type": "eco.xoptics.attenuator_aramis:AttenuatorAramis",
    },
    {
        "name": "ref_laser",
        "args": ["SAROP21-OLAS136"],
        "kwargs": {},
        "z_und": 136,
        "desc": "Bernina beamline reference laser before KBs",
        "type": "eco.xoptics.reflaser:RefLaser_Aramis",
    },
    {
        "name": "slit_att",
        "args": ["SAROP21-OAPU136"],
        "kwargs": {},
        "z_und": 136,
        "desc": "Slits behind attenuator",
        "type": "eco.xoptics.slits:SlitPosWidth",
        "lazy": True,
    },
    # {
        # "name": "slitAtt",
        # "args": ["SAROP21-OAPU136"],
        # "kwargs": {},
        # "z_und": 136,
        # "desc": "Slits behind attenuator",
        # "type": "eco.xoptics.slits:SlitPosWidth_old",
    # },
    {
        "name": "mon_att",
        "args": ["SAROP21-PBPS138"],
        "z_und": 138,
        "desc": "Intensity/Position monitor after Attenuator",
        "type": "eco.xdiagnostics.intensity_monitors:SolidTargetDetectorPBPS",
        "kwargs": {"VME_crate": "SAROP21-CVME-PBPS2", "link": 9},
    },
    {
        "name": "det_dio",
        "args": ["SAROP21-PDIO138"],
        "z_und": 138,
        "desc": "Diode digitizer for exp data",
        "type": "eco.devices_general.detectors:DiodeDigitizer",
        "kwargs": {"VME_crate": "SAROP21-CVME-PBPS2", "link": 9},
    },
    {
        "name": "prof_att",
        "args": ["SAROP21-PPRM138"],
        "kwargs": {},
        "z_und": 138,
        "desc": "Profile monitor after Attenuator",
        "type": "eco.xdiagnostics.profile_monitors:Pprm",
    },
    {
        "name": "kb_ver",
        "args": ["SAROP21-OKBV139"],
        "z_und": 139,
        "desc": "Vertically focusing Bernina KB mirror",
        "type": "eco.xoptics.KBver:KBver",
        "kwargs": {},
    },
    {
        "args": ["SAROP21-OKBH140"],
        "name": "kb_hor",
        "z_und": 140,
        "desc": "Horizontally focusing Bernina KB mirror",
        "type": "eco.xoptics.KBhor:KBhor",
        "kwargs": {},
    },
    # {
        # "name": "slit_kb",
        # "args": [],
        # "kwargs": {"Id": "SARES20"},
        # "z_und": 141,
        # "desc": "Slits behind Kb",
        # "type": "eco.xoptics.slits:SlitBladesJJ_old",
    # },
    {
        "args": [],
        "name": "gps",
        "z_und": 142,
        "desc": "General purpose station",
        "type": "eco.endstations.bernina_diffractometers:GPS",
        "kwargs": {"Id": "SARES22-GPS", "configuration": config["gps_config"]},
    },
    {
        "args": [],
        "name": "xrd",
        "z_und": 142,
        "desc": "Xray diffractometer",
        "type": "eco.endstations.bernina_diffractometers:XRD",
        "kwargs": {"Id": "SARES21-XRD", "configuration": config["xrd_config"]},
    },
    {
        "args": [],
        "name": "xeye",
        "z_und": 142,
        "desc": "Mobile X-ray eye in Bernina hutch",
        "type": "eco.xdiagnostics.profile_monitors:Bernina_XEYE",
        "kwargs": {
            "zoomstage_pv": config["xeye"]["zoomstage_pv"],
            "camera_pv": config["xeye"]["camera_pv"],
            "bshost": "sf-daqsync-01.psi.ch",
            "bsport": 11151,
        },
    },
    {
        "args": [],
        "name": "cams_qioptiq",
        "z_und": 142,
        "desc": "Qioptic sample viewer in Bernina hutch",
        "type": "eco.endstations.bernina_cameras:Qioptiq",
        "kwargs": {
            "bshost": "sf-daqsync-01.psi.ch",
            "bsport": 11149,
            "zoomstage_pv": config["cams_qioptiq"]["zoomstage_pv"],
            "camera_pv": config["cams_qioptiq"]["camera_pv"],
        },
    },
    {
        "args": [],
        "name": "cams_sigma",
        "z_und": 142,
        "desc": "Sigma objective",
        "type": "eco.endstations.bernina_cameras:Sigma",
        "kwargs": {
            "bshost": "sf-daqsync-01.psi.ch",
            "bsport": 11149,
            "camera_pv": config["cams_sigma"]["camera_pv"],
        },
    },
    {
        "args": ["SLAAR02-TSPL-EPL"],
        "name": "phase_shifter",
        "z_und": 142,
        "desc": "Experiment laser phase shifter",
        "type": "eco.devices_general.timing:PhaseShifterAramis",
        "kwargs": {},
    },
    {
        "args": [],
        "name": "las",
        "z_und": 142,
        "desc": "Experiment laser optics",
        "type": "eco.loptics.bernina_experiment:Laser_Exp",
        "kwargs": {"Id": "SLAAR21-LMOT", "smar_config": config["las_smar_config"]},
        "lazy": True,
    },
    {
        "args": ["SLAAR21-LTIM01-EVR0"],
        "name": "laser_shutter",
        "z_und": 142,
        "desc": "Laser Shutter",
        "type": "eco.loptics.laser_shutter:laser_shutter",
        "kwargs": {},
    },
    {
        "args": [],
        "name": "epics_channel_list",
        "desc": "epics channel list",
        "type": "eco.utilities.config:ChannelList",
        "kwargs": {"file_name":"/sf/bernina/config/channel_lists/default_channel_list_epics"},
    },
    {
        "args": [],
        "name": "epics_daq",
        "z_und": 142,
        "desc": "epics data acquisition",
        "type": "eco.acquisition.epics_data:Epicstools",
        "kwargs": {
            "channel_list": Component("epics_channel_list"),
            "default_file_path": f"/sf/bernina/data/{config['pgroup']}/res/epics_daq/",
        },
    },
    {
        "args": [],
        "name": "daq",
        "desc": "server based acquisition",
        "type": "eco.acquisition.dia:DIAClient",
        "kwargs": {
            "instrument": "bernina",
            "api_address": config["daq_address"],
            "pgroup": config["pgroup"],
            "pedestal_directory": config["jf_pedestal_directory"],
            "gain_path": config["jf_gain_path"],
            "config_default": config["daq_dia_config"],
            "jf_channels": config["jf_channels"],
            "default_file_path": None,
        },
    },
    {
        "args": [
            config["checker_PV"],
            config["checker_thresholds"],
            config["checker_fractionInThreshold"],
        ],  #'SARFE10-PBPG050:HAMP-INTENSITY-CAL',[60,700],.7],
        "name": "checker",
        "desc": "checker functions for data acquisition",
        "type": "eco.acquisition.checkers:CheckerCA",
        "kwargs": {},
    },
    {
        "args": [],
        "name": "scans",
        "desc": "server based acquisition",
        "type": "eco.acquisition.scan:Scans",
        "kwargs": {
            "data_base_dir": "scan_data",
            "scan_info_dir": f"/sf/bernina/data/{config['pgroup']}/res/scan_info",
            "default_counters": [Component("daq")],
            "checker": Component("checker"),
            "scan_directories": True,
        },
    },
    {
        "args": [],
        "name": "epics_scans",
        "desc": "epics non beam synchronous based acquisition",
        "type": "eco.acquisition.scan:Scans",
        "kwargs": {
            "data_base_dir": "scan_data",
            "scan_info_dir": f"/sf/bernina/data/{config['pgroup']}/res/epics_daq/scan_info",
            "default_counters": [Component("epics_daq")],
            "checker": Component("checker"),
            "scan_directories": True,
        },
    },
    {
        "args": [],
        "name": "lxt",
        "desc": "laser timing with pockels cells and phase shifter",
        "type": "eco.timing.lasertiming:Lxt",
        "kwargs": {},
    },
    {
        "args": ["SAR-CCTA-ESB"],
        "name": "seq",
        "desc": "sequencer timing application (CTA)",
        "type": "eco.timing.event_timing:CTA_sequencer",
        "kwargs": {},
    },
    {
        "args": ["SIN-TIMAST-TMA"],
        "name": "event_master",
        "desc": "SwissFEL timing master information",
        "type": "eco.timing.event_timing:MasterEventSystem",
        "kwargs": {},
    },
    {
        "args": ["SARES20-CVME-01-EVR0"],
        "name": "evr_bernina",
        "desc": "Bernina event receiver",
        "type": "eco.timing.event_timing:EventReceiver",
        "kwargs": {},
        "lazy": True,
    },
    {
        "args": [],
        "name": "default_channel_list",
        "desc": "Bernina default channels, used in daq",
        "type": "eco.utilities.config:ChannelList",
        "kwargs": {"file_name":"/sf/bernina/config/channel_lists/default_channel_list"},
        "lazy": False,
    },
    {
        "args": [],
        "name": "default_channel_list_bs",
        "desc": "Bernina default bs channels, used by bs_daq",
        "type": "eco.utilities.config:ChannelList",
        "kwargs": {"file_name":"/sf/bernina/config/channel_lists/default_channel_list"},
        "lazy": False,
    },
    {
        "args": [],
        "name": "channels_spectrometer_projection",
        "desc": "",
        "type": "eco.utilities.config:ChannelList",
        "kwargs": {"file_name":"/sf/bernina/config/channel_lists/channel_list_PSSS_projection"},
        "lazy": False,
    },
    {
        "args": [],
        "name": "bs_daq",
        "desc": "bs daq writer (locally!)",
        "type": "eco.acquisition.bs_data:BStools",
        "kwargs": {
            "default_channel_list": {
                "bernina_default_channels_bs": Component("default_channel_list_bs")
            },
            "default_file_path": f"/sf/bernina/data/{config['pgroup']}/res/%s",
        },
        "lazy": False,
    },
]

try:
    components.extend(config["components"])
    print("Did append additional components!")
except:
    print("Could not append components from config.")


#### OLD STUFF TO BE TRANSFERRED OR DELETED ####
components_old = {
    "SARFE10-OPSH044": {
        "alias": "ShutUnd",
        "z_und": 44,
        "desc": "Photon shutter after Undulator",
    },
    "SARFE10-PBIG050": {
        "alias": "GasMon",
        "z_und": 50,
        "desc": "Gas Monitor Intensity",
    },
    "SARFE10-PBPS053": {
        "alias": "MonUnd",
        "z_und": 44,
        "desc": "Intensity position monitor after Undulator",
    },
    "SARFE10-SBST060": {
        "alias": "ShutFE",
        "z_und": 60,
        "desc": "Photon shutter in the end of Front End",
    },
    "SAROP11-OOMH064": {
        "alias": "MirrAlv1",
        "z_und": 64,
        "desc": "Horizontal mirror Alvra 1",
    },
    "SAROP21-PSCR097": {
        "alias": "ProfMirr2",
        "z_und": 97,
        "desc": "Profile Monitor after Mirror 2",
    },
    "SAROP21-OPPI103": {"alias": "Pick", "z_und": 103, "desc": "X-ray pulse picker"},
    "SAROP21-BST114": {
        "alias": "ShutOpt",
        "z_und": 114,
        "desc": "Shutter after Optics hutch",
    },
    "SAROP21-PALM134": {
        "alias": "TimTof",
        "z_und": 134,
        "desc": "Timing diagnostics THz streaking/TOF",
    },
    "SAROP21-PSEN135": {
        "alias": "TimRef",
        "z_und": 135,
        "desc": "Timing diagnostics spectral encoding of ref. index change",
    }
    #        'SLAAR21-LMOT' : {
    #                'alias' : 'Palm',
    #                'z_und' : 142,
    #                'desc' : 'Streaking arrival time monitor',
    #                'eco_type' : 'timing.palm.Palm'},
    #        'SLAAR21-LMOT' : {
    #                'alias' : 'Psen',
    #                'z_und' : 142,
    #                'desc' : 'Streaking arrival time monitor',
    #                'eco_type' : 'timing.psen.Psen'}
    #         = dict(
    #            alias = ''
    #            z_und =
    #            desc = ''},
}
