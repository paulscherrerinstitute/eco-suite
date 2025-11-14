import numpy as np
import h5py
from epics import PV
import os
import datetime
from threading import Thread
from time import sleep
from pathlib import Path
from .utilities import Acquisition
import time
from ..elements.adjustable import AdjustableFS


class RunFilenameGenerator:
    def __init__(self, path, prefix="run", Ndigits=4, separator="_", suffix="json"):
        self.separator = separator
        self.prefix = prefix
        self.Ndigits = Ndigits
        self.path = Path(path)
        self.suffix = suffix

    def get_existing_runnumbers(self):
        fl = self.path.glob(
            self.prefix + self.Ndigits * "[0-9]" + self.separator + "*." + self.suffix
        )
        fl = [tf for tf in fl if tf.is_file()]
        runnos = [
            int(tf.name.split(self.prefix)[1].split(self.separator)[0]) for tf in fl
        ]
        return runnos

    def get_run_info_file(self, runno):
        fl = self.path.glob(
            self.prefix
            + f"{runno:0{self.Ndigits}d}"
            + self.separator
            + "*."
            + self.suffix
        )
        fl = [tf for tf in fl if tf.is_file()]
        if len(fl) > 1:
            raise Exception(
                f"Found multiple files in {self.path} with run number {runno}"
            )
        return fl[0]

    def get_nextrun_number(self):
        runnos = self.get_existing_runnumbers()
        if runnos:
            return max(runnos) + 1
        else:
            return 0

    def get_nextrun_filename(self, name):
        runnos = self.get_existing_runnumbers()
        if runnos:
            runno = max(runnos) + 1
        else:
            runno = 0
        return (
            self.prefix
            + "{{:0{:d}d}}".format(self.Ndigits).format(runno)
            + self.separator
            + name
            + "."
            + self.suffix
        )


class EpicsDaq:
    def __init__(
        self,
        elog=None,
        name=None,
        pgroup=None,
        channel_list=None,
        default_filepath=None,
    ):
        self.name = name
        self.pgroup = pgroup
        self.alternative_file_path = AdjustableFS(
            f"/sf/bernina/config/eco/reference_values/{name}_alternative_file_path.json",
            default_value=False,
            name="alternative_file_path",
        )
        self._elog = elog
        self.channels = {}
        self.pulse_id = PV("SLAAR11-LTIM01-EVR0:RX-PULSEID")
        self.channel_list = channel_list
        self.update_channels()

    @property
    def _default_file_path(self):
        if self.alternative_file_path() == False:
            file_path = f"/sf/bernina/data/{self.pgroup()}/res/run_data/epics_daq/data/"
        else:
            file_path = self.alternative_file_path()
        return file_path

    @_default_file_path.setter
    def _default_file_path(self, val):
        self.alternative_file_path(val)

    def update_channels(self):
        channels = self.channel_list.get_current_value()
        for channel in channels:
            if not (channel in self.channels.keys()):
                self.channels[channel] = PV(channel, auto_monitor=True)

    def h5(self, fina=None, channel_list=None, Npulses=None, queue_size=100):
        if channel_list is None:
            channel_list = self.channel_list
        if not channel_list.get_current_value() == list(self.channels.keys()):
            self.update_channels()

        if os.path.isfile(fina):
            print("!!! File %s already exists, would you like to delete it?" % fina)
            if input("(y/n)") == "y":
                print("Deleting %s ." % fina)
                os.remove(fina)
            else:
                return

        data = self.get_data(channel_list=None, Npulses=None, queue_size=100)

        f = h5py.File(name=fina, mode="w")
        for k in data.keys():
            dat = f.create_group(name=k)
            dat.create_dataset(name="data", data=data[k]["values"])
            dat.create_dataset(name="timestamps", data=data[k]["timestamps"])
            dat.create_dataset(
                name="pulse_id", data=np.arange(Npulses) + round(time.time() * 100)
            )
        return data

    def get_data(self, channel_list=None, Npulses=None, queue_size=100, **kwargs):
        if channel_list is None:
            channel_list = self.channel_list
        if not channel_list.get_current_value() == list(self.channels.keys()):
            self.update_channels()

        data = {}
        counters = {}
        channels = self.channels
        for k, channel in channels.items():
            channelval = channel.value
            if type(channelval) == np.ndarray:
                shape = (Npulses,) + channelval.shape
                dtype = channelval.dtype
            else:
                shape = (Npulses,)
                dtype = type(channelval)

            data[k] = {
                "values": np.ndarray(
                    shape,
                    dtype=dtype,
                ),
                "timestamps": np.ndarray(
                    (Npulses,),
                    dtype=float,
                ),
            }

            counters[k] = 0

        def cb_getdata(ch=None, k="", *args, **kwargs):
            data[k]["values"][counters[k]] = kwargs["value"]
            data[k]["timestamps"][counters[k]] = kwargs["timestamp"]
            counters[k] = counters[k] + 1
            if counters[k] == Npulses:
                ch.clear_callbacks()

        for k, channel in channels.items():
            channel.add_callback(callback=cb_getdata, ch=channel, k=k)
        while True:
            sleep(0.005)
            if np.mean(list(counters.values())) == Npulses:
                break

        return data

    # def acquire(self, Npulses=100, default_path=True, scan=None):
    #    file_name = scan._description
    #    file_name += ".h5"
    #    if default_path:
    #        file_name = self._default_file_path + file_name
    #    data_dir = Path(os.path.dirname(file_name))

    #    if not data_dir.exists():
    #        print(
    #            f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
    #        )
    #        data_dir.mkdir(parents=True)
    #        print(f"Tried to create {data_dir.absolute().as_posix()}")
    #        data_dir.chmod(0o775)
    #        print(f"Tried to change permissions to 775")
    #
    #    def acquire():
    #        self.h5(fina=file_name, Npulses=Npulses)

    #    return Acquisition(
    #        acquire=acquire,
    #        acquisition_kwargs={"file_names": [file_name], "Npulses": Npulses},
    #        hold=False,
    #    )

    def acquire(self, scan=None, Npulses=None, **kwargs):
        acq_pars = {}

        if scan:
            scan_wr = weakref.ref(scan)
            acq_pars = {
                "scan_info": {
                    "scan_name": scan.description(),
                    "scan_values": scan.values_current_step,
                    "scan_readbacks": scan.readbacks_current_step,
                    "name": [adj.name for adj in scan.adjustables],
                    "expected_total_number_of_steps": scan.number_of_steps(),
                    "scan_step_info": {
                        "step_number": scan.next_step + 1,
                    },
                },
            }

        acquisition = Acquisition(
            acquire=None,
            acquisition_kwargs={"Npulses": Npulses},
        )

        def acquire():
            t_tmp = time.time()
            det_val = self.get_data(Npulses)
            scan_wr().detector_values.append(det_val)
            t_stop = time.time()
            scan_wr().timestamp_intervals.append(StepTime(t_tmp, t_stop))

        acquisition.set_acquire_foo(acquire, hold=False)

        return acquisition

    def create_arrays(self, scan, **kwargs):
        scan.monitor_scan_arrays = {}
        for monname, mon in scan.monitors.items():
            scan.monitor_scan_arrays[monname] = ArrayTimestamps(
                data=mon.data["values"],
                timestamps=mon.data["timestamps"],
                timestamp_intervals=scan.timestamp_intervals,
                parameter=parameter_from_scan(scan),
                name=monname,
            )

    def wait_done(self):
        self.check_running()
        self.check_still_running()


class Epicstools:
    def __init__(
        self,
        default_channel_list={"listname": []},
        default_file_path="%s",
        elog=None,
        name=None,
        channel_list=None,
    ):
        self.name = name
        self._default_file_path = default_file_path
        self._default_channel_list = default_channel_list
        self._elog = elog
        self.channels = []
        self.pulse_id = PV("SLAAR11-LTIM01-EVR0:RX-PULSEID")

        if not channel_list:

            print("No channels specified, using all lists instead.")
            channel_list = []
            for tlist in self._default_channel_list.values():
                channel_list.extend(tlist)
        else:
            self.channel_list = channel_list
        for channel in self.channel_list:
            self.channels.append(PV(channel, auto_monitor=True))

    def h5(self, fina=None, channel_list=None, Npulses=None, queue_size=100):
        channel_list = self.channel_list

        if os.path.isfile(fina):
            print("!!! File %s already exists, would you like to delete it?" % fina)
            if input("(y/n)") == "y":
                print("Deleting %s ." % fina)
                os.remove(fina)
            else:
                return

        data = []
        counters = []
        channels = self.channels
        for channel in channels:
            channelval = channel.value
            if type(channelval) == np.ndarray:
                shape = (Npulses,) + channelval.shape
                dtype = channelval.dtype
            else:
                shape = (Npulses,)
                dtype = type(channelval)
            data.append(np.ndarray(shape, dtype=dtype))
            counters.append(0)

        def cb_getdata(ch=None, m=0, *args, **kwargs):
            data[m][counters[m]] = kwargs["value"]
            counters[m] = counters[m] + 1
            if counters[m] == Npulses:
                ch.clear_callbacks()

        for m, channel in enumerate(channels):
            channel.add_callback(callback=cb_getdata, ch=channel, m=m)
        while True:
            sleep(0.005)
            if np.mean(counters) == Npulses:
                break

        f = h5py.File(name=fina, mode="w")
        for n, channel in enumerate(channel_list):
            dat = f.create_group(name=channel)
            dat.create_dataset(name="data", data=data[n])
            dat.create_dataset(
                name="pulse_id", data=np.arange(Npulses) + round(time.time() * 100)
            )
        return data

    def acquire(self, file_name=None, Npulses=100, default_path=True):
        file_name += ".h5"
        if default_path:
            file_name = self._default_file_path + file_name
        data_dir = Path(os.path.dirname(file_name))

        if not data_dir.exists():
            print(
                f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
            )
            data_dir.mkdir(parents=True)
            print(f"Tried to create {data_dir.absolute().as_posix()}")
            data_dir.chmod(0o775)
            print(f"Tried to change permissions to 775")

        def acquire():
            self.h5(fina=file_name, Npulses=Npulses)

        return Acquisition(
            acquire=acquire,
            acquisition_kwargs={"file_names": [file_name], "Npulses": Npulses},
            hold=False,
        )

    def wait_done(self):
        self.check_running()
        self.check_still_running()
