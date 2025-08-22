from numbers import Number
import numpy as np
import matplotlib.pyplot as plt


class ArrayTimestamps:
    def __init__(
        self, data, timestamps, timestamp_intervals=None, parameter=None, name="none"
    ):
        self.data = np.asarray(data)
        self.timestamps = np.asarray(timestamps)
        self.name = name
        self.scan = ScanTimestamps(
            parameter=parameter, timestamp_intervals=timestamp_intervals, array=self
        )

    @property
    def shape(self, *args, **kwargs):
        return self.data.shape

    @property
    def ndim(self, *args, **kwargs):
        return self.data.ndim

    @property
    def ndim_nonzero(self, *args, **kwargs):
        return len(np.asarray(self.shape)[np.nonzero(self.shape)[0]])

    def __len__(self):
        return len(self.timestamps)


class ScanTimestamps:
    def __init__(self, parameter={}, timestamp_intervals=None, array=None, data=None):
        self.timestamp_intervals = timestamp_intervals
        if parameter:
            for par, pardict in parameter.items():
                if not len(pardict["values"]) == len(self):
                    raise Exception(
                        f"Parameter array length of {par} does not fit the defined steps."
                    )
        else:
            parameter = {"none": {"values": [1] * len(timestamp_intervals)}}
        self.parameter = parameter
        self._array = array
        # self._add_methods()

        if data is not None:
            self._data = data

    def append_parameter(self, parameter: {"par_name": {"values": list}}):
        for par, pardict in parameter.items():
            if not len(pardict["values"]) == len(self):
                lenthis = len(pardict["values"])
                raise Exception(
                    f"Parameter array length of {par} ({lenthis}) does not fit the defined steps ({len(self)})."
                )
        self.parameter.update(parameter)

    def count(self):
        return [len(step) for step in self]

    @property
    def par_steps(self):
        data = {name: value["values"] for name, value in self.parameter.items()}
        data.update({"step_length": self.step_lengths})
        return pd.DataFrame(data, index=list(range(len(self))))

    def append_step(self, parameter, timestamp_interval):
        self.timestamp_intervals.append(timestamp_interval)
        for par, pardict in parameter:
            self.parameter[par]["values"].append(pardict["values"])

    def __len__(self):
        return len(self.timestamp_intervals)

    def __getitem__(self, sel):
        """array getter for scan"""
        if isinstance(sel, slice):
            sel = range(*sel.indices(len(self)))
        if isinstance(sel, Number):
            if sel < 0:
                sel = len(self) + sel
            return self.get_step_array(sel)
        else:
            return concatenate([self.get_step_array(n) for n in sel])

    def get_step_array(self, n):
        """array getter for scan"""
        assert n >= 0, "Step index needs to be positive"
        if n == 0 and self.timestamp_intervals is None:
            data = self._array.data[:]
            timestamps = self._array.timestamps[:]
            timestamp_intervals = self._array.timestamp_intervals
            # parameter = self._array.parameter

        # assert not self.step_lengths is None, "No step sizes defined."
        elif not n < len(self.timestamp_intervals):
            raise IndexError(f"Only {len(self.timestamp_intervals)} steps")
        else:
            interval = self.timestamp_intervals[n]
            inds_all = np.searchsorted(interval, self._array.timestamps)
            indmin = (inds_all == 0).nonzero()[0][-1]
            if 2 in inds_all:
                indmax = (inds_all == 2).nonzero()[0][0]
            else:
                indmax = len(self._array.timestamps)
            inds = (indmin, indmax)
            data = self._array.data[slice(*inds)]
            timestamps = self._array.timestamps[slice(*inds)]
            timestamp_intervals = [self.timestamp_intervals[n]]
            parameter = {}
            for par_name, par in self.parameter.items():
                parameter[par_name] = {}
                parameter[par_name]["values"] = [par["values"][n]]
                if "attributes" in par.keys():
                    parameter[par_name]["attributes"] = par["attributes"]
        return ArrayTimestamps(
            data=data,
            timestamps=timestamps,
            parameter=parameter,
            timestamp_intervals=timestamp_intervals,
        )

    # def get_step_indexes(self, ix_step):  # TODO
    #     """ "array getter for multiple steps, more efficient than get_step_array"""
    #     ix_to = np.cumsum(self.step_lengths)
    #     ix_from = np.hstack([np.asarray([0]), ix_to[:-1]])
    #     index_sel = np.concatenate(
    #         [
    #             self._array.index[fr:to]
    #             for fr, to in zip(ix_from[ix_step], ix_to[ix_step])
    #         ],
    #         axis=0,
    #     )
    #     return self._array[np.in1d(self._array.index, index_sel).nonzero()[0]]

    def _check_consistency(self):
        for par, pardict in self.parameter.items():
            if not len(self) == len(pardict["values"]):
                raise Exception(f"Scan length does not fit parameter {par}")

    def get_parameter_selection(self, selection):
        selection = np.atleast_1d(selection)
        if selection.dtype == bool:
            selection = selection.nonzero()[0]
        par_out = {}
        for par, pardict in self.parameter.items():
            par_out[par] = {}
            par_out[par]["values"] = [pardict["values"][i] for i in selection]
            if "attributes" in pardict.keys():
                par_out[par]["attributes"] = pardict["attributes"]
        return par_out

    def plot(
        self,
        scanpar_name=None,
        norm_samples=True,
        axis=None,
        use_quantiles=True,
        *args,
        **kwargs,
    ):
        if not scanpar_name:
            names = list(self.parameter.keys())
            scanpar_name = names[0]
        x = np.asarray(self.parameter[scanpar_name]["values"]).ravel()

        if use_quantiles:
            tmp = np.asarray(
                [
                    np.nanquantile(
                        tstep.data,
                        [
                            0.5,
                            0.5 - 0.682689492137 / 2,
                            0.5 + 0.682689492137 / 2,
                        ],
                    )
                    for tstep in self
                ]
            )
            y = tmp[:, 0]
            ystd = np.diff(tmp[:, 1:], axis=1)[:, 0] / 2
        else:
            y = np.asarray([np.nanmean(tstep.data, axis=0) for tstep in self]).ravel()
            ystd = np.asarray([np.nanstd(tstep.data, axis=0) for tstep in self]).ravel()
        if norm_samples:
            yerr = ystd / np.sqrt(np.asarray(self.count()))
        else:
            yerr = ystd
        if not axis:
            axis = plt.gca()
        axis.errorbar(x, y, yerr=yerr, *args, **kwargs)
        axis.set_xlabel(scanpar_name)
        if self._array.name:
            axis.set_ylabel(self._array.name)

    # def nansum(self, *args, **kwargs):
    #     return [step.nansum(*args, **kwargs) for step in self]

    # def nanmean(self, *args, **kwargs):
    #     return [step.nanmean(*args, **kwargs) for step in self]

    # def nanstd(self, *args, **kwargs):
    #     return [step.nanstd(*args, **kwargs) for step in self]

    # def nanmedian(self, *args, **kwargs):
    #     return [step.nanmedian(*args, **kwargs) for step in self]

    # def nanpercentile(self, *args, **kwargs):
    #     return [step.nanpercentile(*args, **kwargs) for step in self]

    # def nanquantile(self, *args, **kwargs):
    #     return [step.nanquantile(*args, **kwargs) for step in self]

    # def nanmin(self, *args, **kwargs):
    #     return [step.nanmin(*args, **kwargs) for step in self]

    # def nanmax(self, *args, **kwargs):
    #     return [step.nanmax(*args, **kwargs) for step in self]

    # def sum(self, *args, **kwargs):
    #     return [step.sum(*args, **kwargs) for step in self]

    # def mean(self, *args, **kwargs):
    #     return [step.mean(*args, **kwargs) for step in self]

    # def average(self, *args, **kwargs):
    #     return [step.average(*args, **kwargs) for step in self]

    # def std(self, *args, **kwargs):
    #     return [step.std(*args, **kwargs) for step in self]

    # def median(self, *args, **kwargs):
    #     return [step.median(*args, **kwargs) for step in self]

    # def min(self, *args, **kwargs):
    #     return [step.min(*args, **kwargs) for step in self]

    # def max(self, *args, **kwargs):
    #     return [step.max(*args, **kwargs) for step in self]

    # def any(self, *args, **kwargs):
    #     return [step.any(*args, **kwargs) for step in self]

    # def all(self, *args, **kwargs):
    #     return [step.all(*args, **kwargs) for step in self]

    # def count(self):
    #     return [len(step) for step in self]

    # def nancount(self): ...

    # # TODO

    # def median_and_mad(self, axis=None, k_dist=1.4826, norm_samples=False):
    #     """Calculate median and median absolute deviation for steps of a scan.

    #     Args:
    #         axis (int, sequence of int, None, optional): axis argument for median calls.
    #         k_dist (float, optional): distribution scale factor, should be
    #             1 for real MAD.
    #             Defaults to 1.4826 for gaussian distribution.
    #     """
    #     # if self._array.is_dask_array():
    #     #     absfoo = da.abs
    #     # else:
    #     #     absfoo = np.abs

    #     med = [step.median(axis=axis) for step in self]
    #     mad = [
    #         (((step - tmed).abs()) * k_dist).median(axis=axis)
    #         for step, tmed in zip(self, med)
    #     ]
    #     if norm_samples:
    #         mad = [tmad / da.sqrt(ct) for tmad, ct in zip(mad, self.count())]
    #     return med, mad
