from .scan import StepScan
import numpy as np
from numpy.random import RandomState


# class Scan:
#     def __init__(
#         self,
#         adjustables,
#         values,
#         counterCallers,
#         fina,
#         Npulses=100,
#         basepath="",
#         scan_info_dir="",
#         checker=None,
#         scan_directories=False,
#         callbackStartStep=None,
#         checker_sleep_time=0.2,
#         return_at_end="question",
#         run_table=None,
#         elog=None,
#     ):


class ScanND:
    def __init__(
        self,
        adjustables,
        arrays,
        counters,
        fina,
        Npulses=100,
        basepath="",
        scan_info_dir="",
        checker=None,
        scan_directories=False,
        cb_start_scan=None,
        cb_start_step=None,
        cb_end_step=None,
        cb_end_scan=None,
        checker_sleep_time=0.2,
        return_at_end="question",
        run_table=None,
        elog=None,
    ):
        scan_array = []
        scan_adjustables = []
        for n_dim, (adj_tdim, arr_tdim) in enumerate(zip(adjustables, arrays)):
            # check if the dimension is a multi adjustable thing
            try:
                iter(adj_tdim)
                if not len(adj_tdim) == len(arr_tdim):
                    raise Exception(
                        f"arrays and adjusbles in dimension {n_dim}don't match"
                    )
                if len(set([len(sarr) for sarr in arr_tdim])) > 1:
                    raise Exception(
                        f"subarrays in dimension {n_dim} do not have equal length!"
                    )
                scan_array.append(tuple(arr_tdim))
                scan_adjustables.append(tuple(adj_tdim))
            except TypeError:
                scan_array.append(tuple([arr_tdim]))
                scan_adjustables.append(tuple([adj_tdim]))
        self.scan_adjustables = scan_adjustables
        self.scan_array = scan_array
        self.scan_dimension = n_dim + 1

    @property
    def steps_total(self):
        return np.prod([len(ta[0]) for ta in self.scan_array])

    @property
    def shape(self):
        return tuple([len(ta[0]) for ta in self.scan_array])

    def create_stepping_order(self, order="C"):
        return [
            tuple(te)
            for te in np.vstack(
                np.unravel_index(np.arange(self.steps_total), self.shape, order=order)
            ).T
        ]

    def create_random_selection(
        self,
        N_elements=None,
        scan_percentage=None,
        random_type=equal,
        sort_dimensions=False,
    ):

        rs = RandomState(seed=0)
        rs.choice(a, 5, p=np.exp(-a) / sum(np.exp(-a)), replace=False)
