from .scan import Scan


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
        Npulses=100,
        basepath="",
        scan_info_dir="",
        checker=None,
        scan_directories=False,
        callbackStartStep=None,
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
