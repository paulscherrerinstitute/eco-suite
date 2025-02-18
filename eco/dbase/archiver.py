from data_api import get_data, search
from ..epics.detector import DetectorPvDataStream
from fnmatch import translate
import datetime, dateutil
from numbers import Number
from matplotlib import pyplot as plt
import numpy as np
from .. import ecocnf
from ..elements.assembly import Assembly
from datahub import DataBuffer, Table, Stdout


class DataHub(Assembly):
    def __init__(self, pv_pulse_id=None, name=None, add_to_cnf=False):
        super().__init__(name=name)
        if pv_pulse_id:
            self._append(DetectorPvDataStream, pv_pulse_id, name="pulse_id")
        if add_to_cnf:
            ecocnf.archiver = self
        self._databuffer = None

    @property
    def databuffer(self):
        if self._databuffer is None:
            self._databuffer = DataBuffer(backend="sf-databuffer")
        return self._databuffer

    def get_data(self, channels, start, end, range_type=None):
        table = Table()
        self.databuffer.add_listener(table)
        self.databuffer.request(dict(channels=channels, start=start, end=end))
        op = table.as_dataframe()
        self.databuffer.remove_listeners()
        return op

    def get_data_time_range(
        self,
        channels=[],
        start=None,
        end=None,
        plot=False,
        force_type=None,
        labels=None,
        convert_timezone=False,
        **kwargs,
    ):
        if not end:
            end = datetime.datetime.now()
            if isinstance(start, datetime.timedelta):
                start = end + start
            elif isinstance(start, dict):
                start = datetime.timedelta(**start)
                start = end + start
            elif isinstance(start, Number):
                start = datetime.timedelta(seconds=start)
                start = end + start
            else:
                start = datetime.timedelta(**kwargs)
                start = end + start

        if force_type:
            archive_types = ["CA", "BS"]
            if force_type in archive_types:
                if force_type == "CA":
                    channels_req = [f"sf-archiverappliance/{tch}" for tch in channels]
                elif force_type == "BS":
                    channels_req = [f"sf-databuffer/{tch}" for tch in channels]
            else:
                raise Exception(f"force_type must be one of {archive_types}")
        else:
            channels_req = channels

        if type(start) is str:
            start = dateutil.parser.parse(start)
        if type(end) is str:
            end = dateutil.parser.parse(end)

        start = datetime2str(local2utc(start))
        end = datetime2str(local2utc(end))

        data = self.get_data(channels_req, start=start, end=end, range_type="time")
        if convert_timezone:
            data.index = data.index.tz_convert("Europe/Zurich")

        if plot:
            ah = plt.gca()
            if not labels:
                labels = channels
            for chan, label in zip(channels, labels):
                sel = ~data[chan].isnull()
                if any(sel):
                    x = data.index[sel]
                    y = data[chan][sel]
                    ah.step(x, y, ".-", label=label, where="post")
            plt.xticks(rotation=30)
            plt.legend()
            plt.tight_layout()
            plt.xlabel(data.index.name)
            ah.figure.tight_layout()
        return data

    def get_data_pulse_id_range(
        self,
        channels=[],
        start=None,
        end=None,
        plot=False,
        force_type=None,
        convert_timezone=False,
        labels=None,
    ):
        if not end:
            if hasattr(self, "pulse_id"):
                end = int(self.pulse_id.get_current_value())
            else:
                raise Exception("no end pulse id provided")
            start = start + end
        if force_type:
            archive_types = ["CA", "BS"]
            if force_type in archive_types:
                if force_type == "CA":
                    channels_req = [f"sf-archiverappliance/{tch}" for tch in channels]
                elif force_type == "BS":
                    channels_req = [f"sf-databuffer/{tch}" for tch in channels]
            else:
                raise Exception(f"force_type must be one of {archive_types}")
        else:
            channels_req = channels

        data = self.get_data(channels_req, start=start, end=end, range_type="pulseId")
        if convert_timezone:
            data.index = data.index.tz_convert("Europe/Zurich")
        if plot:
            ah = plt.gca()
            if not labels:
                labels = channels
            for chan, label in zip(channels, labels):
                sel = ~np.isnan(data[chan])
                x = data.index[sel]
                y = data[chan][sel]
                ah.step(x, y, ".-", label=label, where="post")
            plt.xticks(rotation=30)
            plt.legend()
            plt.tight_layout()
            plt.xlabel(data.index.name)
            ah.figure.tight_layout()

        return data

    def search(self, searchstring):
        """A search in database using simpler unix glob expressions (e.g. '*ARES*')"""
        return search(translate(searchstring))


class DataApi(Assembly):
    def __init__(self, pv_pulse_id=None, name=None, add_to_cnf=True):
        super().__init__(name=name)
        if pv_pulse_id:
            self._append(DetectorPvDataStream, pv_pulse_id, name="pulse_id")
        if add_to_cnf:
            ecocnf.archiver = self

    def get_data_time_range(
        self,
        channels=[],
        start=None,
        end=None,
        plot=False,
        force_type=None,
        labels=None,
        convert_timezone=True,
        **kwargs,
    ):
        if not end:
            end = datetime.datetime.now()
            if isinstance(start, datetime.timedelta):
                start = end + start
            elif isinstance(start, dict):
                start = datetime.timedelta(**start)
                start = end + start
            elif isinstance(start, Number):
                start = datetime.timedelta(seconds=start)
                start = end + start
            else:
                start = datetime.timedelta(**kwargs)
                start = end + start

        if force_type:
            archive_types = ["CA", "BS"]
            if force_type in archive_types:
                if force_type == "CA":
                    channels_req = [f"sf-archiverappliance/{tch}" for tch in channels]
                elif force_type == "BS":
                    channels_req = [f"sf-databuffer/{tch}" for tch in channels]
            else:
                raise Exception(f"force_type must be one of {archive_types}")
        else:
            channels_req = channels

        if type(start) is str:
            start = dateutil.parser.parse(start)
        if type(end) is str:
            end = dateutil.parser.parse(end)

        start = datetime2str(local2utc(start))
        end = datetime2str(local2utc(end))

        data = get_data(channels_req, start=start, end=end, range_type="time")
        if convert_timezone:
            data.index = data.index.tz_convert("Europe/Zurich")

        if plot:
            ah = plt.gca()
            if not labels:
                labels = channels
            for chan, label in zip(channels, labels):
                sel = ~data[chan].isnull()
                if any(sel):
                    x = data.index[sel]
                    y = data[chan][sel]
                    ah.step(x, y, ".-", label=label, where="post")
            plt.xticks(rotation=30)
            plt.legend()
            plt.tight_layout()
            plt.xlabel(data.index.name)
            ah.figure.tight_layout()
        return data

    def get_data_pulse_id_range(
        self,
        channels=[],
        start=None,
        end=None,
        plot=False,
        force_type=None,
        convert_timezone=True,
        labels=None,
    ):
        if not end:
            if hasattr(self, "pulse_id"):
                end = int(self.pulse_id.get_current_value())
            else:
                raise Exception("no end pulse id provided")
            start = start + end
        if force_type:
            archive_types = ["CA", "BS"]
            if force_type in archive_types:
                if force_type == "CA":
                    channels_req = [f"sf-archiverappliance/{tch}" for tch in channels]
                elif force_type == "BS":
                    channels_req = [f"sf-databuffer/{tch}" for tch in channels]
            else:
                raise Exception(f"force_type must be one of {archive_types}")
        else:
            channels_req = channels

        data = get_data(channels_req, start=start, end=end, range_type="pulseId")
        if convert_timezone:
            data.index = data.index.tz_convert("Europe/Zurich")
        if plot:
            ah = plt.gca()
            if not labels:
                labels = channels
            for chan, label in zip(channels, labels):
                sel = ~np.isnan(data[chan])
                x = data.index[sel]
                y = data[chan][sel]
                ah.step(x, y, ".-", label=label, where="post")
            plt.xticks(rotation=30)
            plt.legend()
            plt.tight_layout()
            plt.xlabel(data.index.name)
            ah.figure.tight_layout()

        return data

    def search(self, searchstring):
        """A search in database using simpler unix glob expressions (e.g. '*ARES*')"""
        return search(translate(searchstring))


def datetime2str(datetime_date):
    return datetime_date.isoformat()


def local2utc(datetime_date):

    return datetime_date.replace(
        tzinfo=None,
    ).astimezone(
        tz=datetime.timezone.utc,
    )
