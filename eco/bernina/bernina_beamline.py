
# new beamline startup

from eco import Assembly
from eco.xoptics.attenuator_aramis import AttenuatorAramis


namespace.append_obj(
    "Att_usd",
    name="att_usd",
    module_name="eco.xoptics.att_usd",
    xp=xp,
    lazy=True,
)

namespace.append_obj(
    "SlitPosWidth",
    "SAROP21-OAPU138",
    name="slit_att",
    lazy=True,
    module_name="eco.xoptics.slits",
),

namespace.append_obj(
    "JJSlitUnd",
    name="slit_und",
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU092",
    name="slit_switch",
    module_name="eco.xoptics.slits",
    lazy=True,
)
namespace.append_obj(
    "SlitBlades",
    "SAROP21-OAPU102",
    name="slit_mono",
    module_name="eco.xoptics.slits",
    lazy=True,
)


    {
        "name": "pshut_und",
        "type": "eco.xoptics.shutters:PhotonShutter",
        "args": ["SARFE10-OPSH044:REQUEST"],
        "kwargs": {},
        "z_und": 44,
        "desc": "First shutter after Undulators",
    },

    {
        "name": "xp",
        "args": [],
        "kwargs": {
            "Id": "SAROP21-OPPI113",
            "evronoff": "SGE-CPCW-72-EVR0:FrontUnivOut15-Ena-SP",
            "evrsrc": "SGE-CPCW-72-EVR0:FrontUnivOut15-Src-SP",
        },
        "z_und": 103,
        "desc": "X-ray pulse picker",
        "type": "eco.xoptics.pp:Pulsepick",
    },


    {
        "name": "att_fe",
        "type": "eco.xoptics.attenuator_aramis:AttenuatorAramis",
        "args": ["SARFE10-OATT053"],
        "kwargs": {"shutter": Component("pshut_und")},
        "z_und": 53,
        "desc": "Attenuator in Front End",
    },


class AttenuationFELBernina(Assembly):
    def __init__(self, name=None):
        super().__init__(name=name)
        self._append(
            AttenuatorAramis, "SAROP21-OATT135", set_limits=[], name="opt", shutter=None
        )
        self._append(
            AttenuatorAramis, "SARFE10-OATT053", set_limits=[], name="fe", shutter=None
        )
