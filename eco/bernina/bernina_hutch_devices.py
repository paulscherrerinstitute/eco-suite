from eco.bernina.bernina import namespace
from eco.utilities.config import NamespaceComponent




namespace.append_obj(
    "DigitizerKeysight",
    # "SARES21-GES1", # normal one
    "SARES22-GES1",  # not quite clear, old PALM?
    name="digitizer_keysight_user",
    module_name="eco.devices_general.digitizers",
    lazy=True,
)
namespace.append_obj(
    "DigitizerIoxos",
    "SARES20-LSCP9-FNS",
    name="digitizer_ioxos_user",
    module_name="eco.devices_general.digitizers",
    lazy=True,
)
namespace.append_obj(
    "DigitizerIoxos",
    "SLAAR21-LSCP1-FNS",
    name="digitizer_ioxos_laser",
    module_name="eco.devices_general.digitizers",
    lazy=True,
)

namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-n",
    lazy=True,
    name="cam_north",
    module_name="eco.devices_general.cameras_ptz",
    # click-to-center/drag-to-zoom otherwise land diagonally opposite
    # where clicked on this unit -- see AxisPTZ._to_sensor_xy
    invert_click_x=True,
    invert_click_y=True,
)
namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-w",
    lazy=True,
    name="cam_west",
    module_name="eco.devices_general.cameras_ptz",
    # click-to-center/drag-to-zoom otherwise land diagonally opposite
    # where clicked on this unit -- see AxisPTZ._to_sensor_xy
    invert_click_x=True,
    invert_click_y=True,
)
namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-s",
    lazy=True,
    name="cam_south",
    module_name="eco.devices_general.cameras_ptz",
    # click-to-center/drag-to-zoom otherwise land diagonally opposite
    # where clicked on this unit -- see AxisPTZ._to_sensor_xy
    invert_click_x=True,
    invert_click_y=True,
)


namespace.append_obj(
    "WagoAnalogInputs",
    "SARES20-CWAG-GPS01",
    lazy=True,
    name="analog_inputs",
    module_name="eco.devices_general.wago",
)
namespace.append_obj(
    "WagoAnalogOutputs",
    "SARES20-CWAG-GPS01",
    lazy=True,
    name="analog_outputs",
    module_name="eco.devices_general.wago",
)
# One physical-controller-box view of hardware otherwise only wired up one
# channel at a time under function-specific names (like analog_inputs/
# analog_outputs just above) -- additive, nothing above is replaced. See
# eco/devices_general/controllers/README.md. Deselect this whole branch from
# a default startup the same way as any other top-level name, via
# namespace.select_required_names().
namespace.append_obj(
    "build_bernina_controllers",
    lazy=True,
    name="controllers",
    module_name="eco.devices_general.controllers",
)


namespace.append_obj(
    "SmaractController",
    "SARES20-MCS1:MOT_",
    lazy=True,
    name="smaract_usd",
    module_name="eco.motion.smaract",
)
namespace.append_obj(
    "SmaractController",
    "SARES20-MCS2:MOT_",
    lazy=True,
    name="smaract_user1",
    module_name="eco.motion.smaract",
)
namespace.append_obj(
    "SmaractController",
    "SARES20-MCS3:MOT_",
    lazy=True,
    name="smaract_user2",
    module_name="eco.motion.smaract",
)


namespace.append_obj(
    "GudeStrip",
    "SARES20-CPPS-01",
    lazy=True,
    name="powerstrip_gps",
    module_name="eco.devices_general.powersockets",
)
namespace.append_obj(
    "GudeStrip",
    "SARES20-CPPS-04",
    lazy=True,
    name="powerstrip_xrd",
    module_name="eco.devices_general.powersockets",
)
namespace.append_obj(
    "GudeStrip",
    "SARES20-CPPS-02",
    lazy=True,
    name="powerstrip_patch2",
    module_name="eco.devices_general.powersockets",
)


namespace.append_obj(
    "NEW_MpodModule",
    "SARES20-MPD1",
    [0, 1, 2, 3],
    ["ch1", "ch2", "ch3", "ch4"],
    module_string="1",
    name="power_LV_patch1",
    lazy=True,
    module_name="eco.devices_general.powersockets",
)

namespace.append_obj(
    "NEW_MpodModule",
    "SARES20-MPD1",
    [4, 5, 6, 7],
    ["ch1", "ch2", "ch3", "ch4"],
    module_string="1",
    name="power_LV_patch2",
    lazy=True,
    module_name="eco.devices_general.powersockets",
)

namespace.append_obj(
    "AxisPTZ",
    "bernina-cam-mobile1",
    lazy=True,
    name="cam_mob1",
    module_name="eco.devices_general.cameras_ptz",
)
namespace.append_obj(
    "OxygenSensor",
    "SARES20-CWAG-GPS01:ADC08",
    lazy=True,
    name="oxygen_sensor",
    module_name="eco.devices_general.sensors_ai",
)