import requests
import time
from ..elements.assembly import Assembly
from ..devices_general.adjustable import AdjustableGetSet
from numpy import polyval
import numpy as np
import urllib.request
import io
from PIL import Image
import os
from timg import Renderer, Ansi24HblockMethod
from shutil import get_terminal_size
from enum import IntEnum, auto

# function ptz_slider_onChange(group)
# {
#  if ((group == "pan" && "abs" == "rel") ||
#      (group == "tilt" && "abs" == "rel") ||
#      (group == "zoom" && "abs" == "rel") ||
#      (group == "focus" && "no" == "rel") ||
#      (group == "brightness" && "" == "rel") ||
#      (group == "iris" && "abs" == "rel")) {
#    group = "r" + group;
#  }
#  if (theCameraNumber == "") theCameraNumber = "1";
#  var url = "/axis-cgi/com/ptz.cgi?camera="+theCameraNumber+"&"+group+"="+parseFloat(Math.round(theNewSliderValue * 10)/10);
# }
#
# Looks like 'pan' is an absolute pan where 'rpan' is a relative pan.
#
# curl 'http://<<camera address>>/axis-cgi/com/ptz.cgi?camera=1&continuouspantiltmove=0,0&imagerotation=0&timestamp=1491243098477'


AUTOFOCUS = IntEnum("autofocus", {"on": 1, "off": 0})
AUTOIRIS = IntEnum("autoiris", {"on": 1, "off": 0})


class AxisPTZ(Assembly):
    def __init__(self, camera_address, name="dummycam"):
        super().__init__(name=name)
        self.camera_address = camera_address
        self.camera_n = 1
        self.camera_ir = 0

        self._append(
            AdjustableGetSet,
            lambda: polyval([0.00290058, 0.99709942], self.get_position()["zoom"]),
            lambda val: self.set_par(
                "zoom", int(polyval([344.75862069, -343.75862069], val))
            ),
            precision=10 / 9999 * 30,
            check_interval=0.05,
            name="zoom",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: self.get_position()["tilt"],
            lambda val: self.set_par("tilt", val),
            name="tilt",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: self.get_position()["pan"],
            lambda val: self.set_par("pan", val),
            name="pan",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: (self.get_position()["iris"] - 1) / 9995,
            lambda val: self.set_par("iris", val * 9995 + 1),
            name="iris",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: (self.get_position()["focus"] - 750) / (9999 - 750),
            lambda val: self.set_par("focus", val * (9999 - 750) + 750),
            name="focus",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: AUTOFOCUS.__dict__[self.get_position()["autofocus"]],
            lambda val: self.set_par("autofocus", AUTOFOCUS(val).name),
            name="autofocus",
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            lambda: AUTOFOCUS.__dict__[self.get_position()["autoiris"]],
            lambda val: self.set_par("autoiris", AUTOFOCUS(val).name),
            name="autoiris",
            is_setting=True,
        )

    # camera_n = 1
    # camera_url = 'http://<<camera address>>/axis-cgi/com/ptz.cgi'
    # camera_ir = 0

    # presets = {
    # 	'Home':
    # 		{
    # 			'pan'			: -120.7629,
    # 			'tilt'			: -4.8568,
    # 			'zoom'			: 696.0,
    # 			'brightness' 	: 3333.0,
    # 			#'autofocus'		: 'on',
    # 			#'autoiris' 		: 'on',
    # 			#'focus'		 : 6424.0,	# generates error
    # 			#'iris'			 : 2739.0,
    # 		},
    # 	'Mt. Washington':
    # 	#value="tilt=154749:focus=32766.000000:pan=267468:iris=32766.000000:zoom=11111.000000"
    # 		{
    # 			'pan'			: 156.7195,
    # 			'tilt'			: -0.6732,
    # 			'zoom'			: 11111.0,
    # 			#'autofocus'		: 'on',
    # 			#'autoiris' 		: 'on',
    # 			#'focus'		 : 7964.0,
    # 			#'iris'			 : 2583.0,
    # 		}
    # }
    @property
    def camera_url(self):
        return f"http://{self.camera_address}/axis-cgi/com/ptz.cgi"

    def get_image(self, filename=None, as_array=False):
        img_url = f"http://{self.camera_address}/jpg/image.jpg"
        with urllib.request.urlopen(img_url) as url:
            f = io.BytesIO(url.read())
        img = Image.open(f)
        if as_array:
            return np.asarray(img)
        else:
            return img

    def show(self, in_terminal=True):
        r = Renderer()
        r.load_image(self.get_image())
        r.resize(get_terminal_size()[0])
        r.render(Ansi24HblockMethod)

    def cameraCmd(self, q_cmd):
        resp_data = {}
        base_q_args = {
            "camera": self.camera_n,
            "imagerotation": self.camera_ir,
            "html": "no",
            "timestamp": int(time.time()),
        }

        q_args = merge_dicts(q_cmd, base_q_args)
        resp = requests.get(self.camera_url, params=q_args)
        if resp.text.startswith("Error"):
            print(resp.text)
        else:
            for line in resp.text.splitlines():
                (name, var) = line.split("=", 2)
                try:
                    resp_data[name.strip()] = float(var)
                except ValueError:
                    resp_data[name.strip()] = var

        return resp_data

    def get_par(self, query):
        # print("cameraGet(" + query + ")")
        return self.cameraCmd({"query": query})

    # 	resp_data = {}
    # 	q_args = { 'query': query,
    # 		'camera': camera_n, 'imagerotation': camera_ir,
    # 		'html': 'no', 'timestamp': int(time.time())
    # 	}
    # 	resp = requests.get(camera_url, params=q_args)
    # 	for line in resp.text.splitlines():
    # 		(name, var) = line.split("=", 2)
    # 		try:
    # 			resp_data[name.strip()] = float(var)
    # 		except ValueError:
    # 			resp_data[name.strip()] = var
    #
    # 	return resp_data

    def set_par(self, group, val):
        print(val)
        # print("cameraSet(" + group + ", " + str(val) + ")")
        return self.cameraCmd({group: val})

    # 	resp_data = {}
    # 	q_args = { group: val,
    # 		'camera': camera_n, 'imagerotation': camera_ir,
    # 		'html': 'no', 'timestamp': int(time.time())
    # 	}
    #
    # 	resp = requests.get(camera_url, params=q_args)
    # 	for line in resp.text.splitlines():
    # 		(name, var) = line.split("=", 2)
    # 		try:
    # 			resp_data[name.strip()] = float(var)
    # 		except ValueError:
    # 			resp_data[name.strip()] = var
    #
    # 	return resp_data

    # def cameraGoToPreset(preset_name):
    #     preset = presets[preset_name]
    #     if preset != None:
    #         for key, value in preset.items():
    #             cameraSet(key, value)

    def home(self):
        return self.cameraCmd({"move": "home"})

    def get_position(self):
        return self.get_par("position")

    def get_limits(self):
        return self.get_par("limits")

    def set_pan(self, value):
        return self.set_par("pan", value)

    def set_tilt(self, value):
        return self.set_par("tilt", value)

    def set_zoom(self, value):
        return self.set_par("zoom", value)

    def set_panrelative(self, value):
        return self.set_par("rpan", value)

    def set_tiltrelative(self, value):
        return self.set_par("rtilt", value)

    def set_zoomrelative(self, value):
        return self.set_par("rzoom", value)


# print("Move to home...")
# print(cameraHome())

# #time.sleep(1)
# print("Get PTZ and Limits...")
# print(cameraGetPTZ())
# print(cameraGetLimits())

# for i in range(5):
# 	time.sleep(1)
# 	print("Move left...")
# 	print(cameraPanRelative(-1))

# for i in range(5):
# 	time.sleep(1)
# 	print("Move right...")
# 	print(cameraPanRelative(1))


# print(cameraTilt(-1))

# time.sleep(5)
# print("Show Mt. Washington...")
# print(cameraGoToPreset('Mt. Washington'))

# time.sleep(2)
# print("Move back home...")
# print(cameraHome())
# print(cameraGoToPreset('Home'))


def merge_dicts(*dict_args):
    """
    Given any number of dicts, shallow copy and merge into a new dict,
    precedence goes to key value pairs in latter dicts.
    """
    result = {}
    for dictionary in dict_args:
        result.update(dictionary)
    return result
