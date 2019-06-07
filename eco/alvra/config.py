elog = {
    "url": "https://elog-gfa.psi.ch/Alvra",
    "screenshot_directory": "/sf/alvra/config/screenshots",
}

aliases = {
    # 	Front-End components
    "SARFE10-OPSH044": {
        "alias": "shutUnd",
        "z_und": 44,
        "desc": "Photon shutter after Undulator",
    },
    "SARFE10-OAPU044": {
        "alias": "slitUnd",
        "z_und": 44,
        "desc": "Slit after Undulator",
        "eco_type": "xoptics.slits.SlitFourBlades_old",
    },
    "SARFE10-PBIG050": {
        "alias": "gasMon",
        "z_und": 50,
        "desc": "Gas Monitor Intensity (PBIG)",
    },
    "SARFE10-PBPS053": {
        "alias": "pbpsUnd",
        "z_und": 44,
        "desc": "Intensity position monitor after Undulator (PBPS)",
    },
    "SARFE10-OATT053": {
        "alias": "attFE",
        "z_und": 53,
        "desc": "Attenuator in Front End",
        "eco_type": "xoptics.attenuator_aramis.AttenuatorAramis",
    },
    "SARFE10-PPRM053": {
        "alias": "screenFE",
        "z_und": 53,
        "desc": "Profile monitor after single-shot spectrometer (PPRM)",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SARFE10-SBST060": {
        "alias": "shutFE",
        "z_und": 60,
        "desc": "Photon shutter in the end of Front End",
    },
    # 	Optics hutch components
    "SARFE10-PPRM064": {
        "alias": "screenOP",
        "z_und": 64,
        "desc": "Profile monitor after Front End",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SAROP11-OOMH064": {
        "alias": "mirrorAlv1",
        "z_und": 64,
        "desc": "First Alvra Horizontal offset mirror (OMH064)",
    },
    "SAROP11-PPRM066": {
        "alias": "screenMirrAlv1",
        "z_und": 66,
        "desc": "Profile monitor after Alvra Mirror 1 (PPRM)",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SAROP11-OOMH076": {
        "alias": "mirrorAlv2",
        "z_und": 76,
        "desc": "Second Alvra Horizontal offset mirror (OMH076)",
    },
    "SAROP11-PPRM078": {
        "alias": "screenMirrAlv2",
        "z_und": 78,
        "desc": "Profile monitor after Alvra Mirror 2 (PPRM)",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SAROP11-OAPU104": {
        "alias": "slitSwitch",
        "z_und": 104,
        "desc": "Slit in Optics hutch after Photon switchyard and before Alvra mono",
        "eco_type": "xoptics.slits.SlitBlades_old",
    },
    "SAROP11-ODCM105": {
        "alias": "mono",
        "z_und": 105,
        "desc": "Alvra DCM Monochromator",
        "eco_type": "xoptics.dcm.Double_Crystal_Mono",
    },
    #         'ALVRA' : {
    #              'alias' : 'mono_FEL',
    #              'z_und' : 105,
    #              'desc' : 'Joint mono-FEL energy device',
    #              'eco_type' : 'xoptics.dcm.alvra_mono_FEL'},
    #         'SAROP11-PSCR106' : {
    #              'alias' : 'ProfMono',
    #              'z_und' : 106,
    #              'desc' : 'Profile Monitor after Mono (PSCR)'},
    "SAROP11-OOMV108": {
        "alias": "mirrorV1",
        "z_und": 108,
        "desc": "Alvra Vertical offset Mirror 1 (OMV108)",
    },
    #         'SAROP11-PSCR109' : {
    #              'alias' : 'ProfMirrV1',
    #              'z_und' : 109,
    #              'desc' : 'Profile Monitor after Vertical Mirror 1 (PSCR)'},
    "SAROP11-OOMV109": {
        "alias": "mirrorV2",
        "z_und": 109,
        "desc": "Alvra Vertical offset Mirror 2 (OMV109)",
    },
    "SAROP11-PPRM110": {
        "alias": "screenMirrorV2",
        "z_und": 110,
        "desc": "Profile monitor after Vertical Mirror 2 (PPRM)",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SAROP11-OPPI110": {
        "alias": "pulsePicker",
        "z_und": 110,
        "desc": "X-ray pulse picker",
    },
    "SAROP11-SBST114": {
        "alias": "shutOpt",
        "z_und": 114,
        "desc": "Shutter after Optics hutch",
    },
    ##	Experimental hutch components
    "SAROP11-PBPS117": {
        "alias": "pbpsOpt",
        "z_und": 117,
        "desc": "Intensity/position monitor after Optics hutch (PBPS)",
        "eco_type": "xdiagnostics.intensity_monitors.SolidTargetDetectorPBPS",
        "kwargs": {"VME_crate": "SAROP11-CVME-PBPS1", "link": 9},
    },
    "SAROP11-PPRM117": {
        "alias": "screenOPEnd",
        "z_und": 117,
        "desc": "Profile monitor after Optics hutch (PPRM)",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SAROP11-PALM118": {
        "alias": "DelayPALM",
        "z_und": 118,
        "desc": "Timing diagnostics THz streaking (PALM)",
    },
    "SAROP11-PSEN119": {
        "alias": "DelayPSEN",
        "z_und": 119,
        "desc": "Timing diagnostics spectral encoding (PSEN)",
    },
    "SAROP11-OATT120": {
        "alias": "attExp",
        "z_und": 120,
        "desc": "Attenuator Alvra",
        "eco_type": "xoptics.attenuator_aramis.AttenuatorAramis",
    },
    "SAROP11-OAPU120": {
        "alias": "slitAttExp",
        "z_und": 120,
        "desc": "Slits behind attenuator",
        "eco_type": "xoptics.slits.SlitPosWidth_old",
    },
    "SAROP11-OLAS120": {
        "alias": "refLaser",
        "z_und": 120,
        "desc": "Alvra beamline reference laser before KBs (OLAS)",
        "eco_type": "xoptics.reflaser.RefLaser_Aramis",
    },
    "SAROP11-PBPS122": {
        "alias": "pbpsAtt",
        "z_und": 122,
        "desc": "Intensity/Position monitor after Attenuator",
        "eco_type": "xdiagnostics.intensity_monitors.SolidTargetDetectorPBPS",
        "kwargs": {"VME_crate": "SAROP11-CVME-PBPS1", "link": 9},
    },
    "SAROP11-PPRM122": {
        "alias": "screenAtt",
        "z_und": 122,
        "desc": "Profile monitor after Attenuator",
        "eco_type": "xdiagnostics.profile_monitors.Pprm",
    },
    "SAROP11-OKBV123": {
        "alias": "kbVer",
        "z_und": 123,
        "desc": "Alvra vertical KB mirror",
        "eco_type": "xoptics.KBver.KBver",
    },
    "SAROP11-OKBH124": {
        "alias": "kbHor",
        "z_und": 124,
        "desc": "Alvra horizontal KB mirror",
        "eco_type": "xoptics.KBhor.KBhor",
    },
    #         'SAROP11-PIPS125-1' : {
    #                 'alias' : 'PIPS1',
    #                 'z_und' : 127,
    #                 'desc' : 'Diode digitizer for PIPS1',
    #             'eco_type' : 'devices_general.detectors.DiodeDigitizer',
    #               'kwargs' : {'VME_crate':'SAROP11-CVME-PBPS1','link':9} },
    #         'SAROP11-PIPS125-2' : {
    #                 'alias' : 'PIPS1',
    #                 'z_und' : 127,
    #                 'desc' : 'Diode digitizer for PIPS2',
    #             'eco_type' : 'devices_general.detectors.DiodeDigitizer',
    #               'kwargs' : {'VME_crate':'SAROP11-CVME-PBPS1','link':9} },
    "SARES11-XSAM125": {
        "alias": "primeSample",
        "z_und": 127,
        "desc": "Sample XYZ manipulator",
        "eco_type": "endstations.alvra_prime.huber",
    },
    "SARES11-XCRY125": {
        "alias": "primeCryTrans",
        "z_und": 127,
        "desc": "Prime von Hamos X-trans (Bragg)",
        "eco_type": "endstations.alvra_prime.vonHamosBragg",
    },
    "SARES11-XOTA125": {
        "alias": "primeTable",
        "z_und": 127,
        "desc": "Prime optical table",
        "eco_type": "endstations.alvra_prime.table",
    },
    "SARES11-XMI125": {
        "alias": "primeMicroscope",
        "z_und": 127,
        "desc": "Microscope focus and zoom",
        "eco_type": "endstations.alvra_prime.microscope",
    },
    #         'SARES22-GPS' : {
    #                 'alias' : 'Gps',
    #                 'z_und' : 142,
    #                 'desc' : 'General purpose station',
    #                 'eco_type' : 'endstations.bernina_gps.GPS'},
    #         'SARES20-PROF142-M1' : {
    #                 'alias' : 'Xeye',
    #                 'z_und' : 142,
    #                 'desc' : 'Mobile X-ray eye in Bernina hutch',
    #                 'eco_type' : 'xdiagnostics.profile_monitors.Bernina_XEYE',
    #                 'kwargs' : {'bshost':'sf-daqsync-01.psi.ch','bsport':11173},
    #
    #                     },
    #         'SLAAR21-LMOT' : {
    #                 'alias' : 'LasExp',
    #                 'z_und' : 127,
    #                 'desc' : 'Experiment laser optics',
    #                 'eco_type' : 'loptics.bernina_experiment.Laser_Exp'},
    "SLAAR01-TSPL-EPL": {
        "alias": "phaseShifter",
        "z_und": 127,
        "desc": "Experiment laser phase shifter (Globi)",
        "eco_type": "devices_general.alvratiming.PhaseShifterAramis",
    },
    "SLAAR11-LMOT": {
        "alias": "laser",
        "z_und": 122,
        "desc": "Experiment laser hardware",
        "eco_type": "loptics.alvra_experiment.Laser_Exp",
    },
    #          'SLAAR11-LMOT' : {
    #                  'alias' : 'palm_eo',
    #                  'z_und' : 119,
    #                  'desc' : 'PALM EO-sampling delay line',
    #                  'eco_type' : 'xdiagnostics.palm.eo'},
    #        'http://sf-daq-4:10000' : {
    #                'alias' : 'DetJF',
    #                'z_und' : 125,
    #                'desc' : '4.5M Jungfrau detector',
    #                'eco_type' : 'devices_general.alvradetectors.DIAClient'},
    "SARES11-V": {
        "alias": "vacuum",
        "z_und": 127,
        "desc": "Prime vacuum system",
        "eco_type": "endstations.alvra_prime.vacuum",
    },
    "SLAAR11-LTIM01-EVR0": {
        "alias": "laserShutter",
        "z_und": 122,
        "desc": "Laser Shutter",
        "eco_type": "loptics.alvra_laser_shutter.laser_shutter",
    },
    #         = dict(
    #            alias = ''
    #            z_und =
    #            desc = ''},
    "SARES11-CMOV-SMA691110": {
        "alias": "_prism_gonio",
        "z_und": 127,
        "desc": "Prime laser prism gonio",
        "eco_type": "devices_general.smaract.SmarActRecord",
        "device": "prism",
        "axis": "gonio",
    },
    "SARES11-CMOV-SMA691111": {
        "alias": "_prism_trans",
        "z_und": 127,
        "desc": "Prime laser prism trans",
        "eco_type": "devices_general.smaract.SmarActRecord",
        "device": "prism",
        "axis": "trans",
    },
    "SARES11-CMOV-SMA691112": {
        "alias": "_prism_rot",
        "z_und": 127,
        "desc": "Prime laser prism rotation",
        "eco_type": "devices_general.smaract.SmarActRecord",
        "device": "prism",  # a virtual stage for eco namespace
        "axis": "rot",
    },  # a axis of this virtual stage
    "SARES11-CMOV-SMA691113": {
        "alias": "_xmic_gon",
        "z_und": 127,
        "desc": "Prime microscope mirror gonio",
        "eco_type": "devices_general.smaract.SmarActRecord",
    },
    # no 'device' becauses its appended to other stage in
    #   ..endstations/alvra_prime.py
    "SARES11-CMOV-SMA691114": {
        "alias": "_xmic_rot",
        "z_und": 127,
        "desc": "Prime microscope mirror rotation",
        "eco_type": "devices_general.smaract.SmarActRecord",
    },
}
