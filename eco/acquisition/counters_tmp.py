




# likely never worked ...
# def _wait_for_tasks(scan, **kwargs):
#     print("checking remaining tasks from previous scan ...")
#     for task in scan.remaining_tasks:
#         task.join()
#     print("... done.")

import json
from pathlib import Path
import shutil
from threading import Thread


class CounterChecker:
    def __init__(self):
        # self.
        if self.checker:
            first_check = time()
            checker_unhappy = False
            while not self.checker.check_now():
                print(
                    colorama.Fore.RED
                    + f"Condition checker is not happy, waiting for OK conditions since {time()-first_check:5.1f} seconds."
                    + colorama.Fore.RESET,
                    # end="\r",
                )
                sleep(self._checker_sleep_time)
                checker_unhappy = True
            if checker_unhappy:
                print(
                    colorama.Fore.RED
                    + f"Condition checker was not happy and waiting for {time()-first_check:5.1f} seconds."
                    + colorama.Fore.RESET
                )
            self.checker.clear_and_start_counting()

class CounterStatusInitNamespaceToDAQ:
    def __init__(self, namespace=None, daq=None):
        self.namespace = namespace
        self.daq = daq
        self.callbacks_start_scan = []
        self.callbacks_end_scan = []
        self.callbacks_start_step = []
        self.callbacks_end_step = []

    def append_start_status_to_scan(self,scan=None, append_status_info=True):
        if not append_status_info:
            return
        namespace_status = self.namespace.get_status(base=None)
        stat = {"status_run_start": namespace_status}
        scan.namespace_status = stat

    def callback_start_step(self, scan=None, append_status_info=True):
        pass
    def callback_end_step(self, scan=None, append_status_info=True):
        pass
    def append_status_to_scan_and(self,
        scan, append_status_info=True, end_scan=True, **kwargs
    ):
        if not append_status_info:
            return
        
        if not len(scan.values_done)>0:
            return
        
        namespace_status = self.namespace.get_status(base=None)
        scan.namespace_status["status_run_end"] = namespace_status
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = self.daq.get_last_run_number()
        
        pgroup = self.daq.pgroup
        tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux")
        tmpdir.mkdir(exist_ok=True, parents=True)
        try:
            tmpdir.chmod(0o775)
        except:
            pass

        statusfile = tmpdir / Path("status.json")
        if not statusfile.exists():
            with open(statusfile, "w") as f:
                json.dump(scan.namespace_status, f, sort_keys=True, cls=NumpyEncoder, indent=4)
        else:
            with open(statusfile, "r+") as f:
                f.seek(0)
                json.dump(scan.namespace_status, f, sort_keys=True, cls=NumpyEncoder, indent=4)
                f.truncate()
                print("Wrote status with seek truncate!")
        if not statusfile.group() == statusfile.parent.group():
            shutil.chown(statusfile, group=statusfile.parent.group())

        response = self.daq.append_aux(
            statusfile.resolve().as_posix(),
            pgroup=pgroup,
            run_number=runno,
        )
        print("####### transfer status #######")
        print(response.json())
        print("###############################")
        scan.scan_info["scan_parameters"]["status"] = "aux/status.json"



class CounterAliasesToDAQ:
    def __init__(self, namespace=None, daq=None):
        self.namespace = namespace
        self.daq = daq

    def callback_start_scan(self, scan=None, append_status_info=True):
        pass

    def callback_end_step(self, scan=None, append_status_info=True):
        pass


    def callback_start_step(self, scan, force=False, **kwargs):
        if force or (len(scan.values_done) == 1):
            namespace_aliases = self.namespace.alias.get_all()
            if hasattr(scan, "daq_run_number"):
                runno = scan.daq_run_number
            else:
                runno = self.daq.get_last_run_number()
            pgroup = self.daq.pgroup
            tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/tmp/aliases_run{runno:04d}")
            tmpdir.mkdir(exist_ok=True, parents=True)
            try:
                tmpdir.chmod(0o775)
            except:
                pass
            aliasfile = tmpdir / Path("aliases.json")
            if not Path(aliasfile).exists():
                with open(aliasfile, "w") as f:
                    json.dump(
                        namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                    )
            else:
                with open(aliasfile, "r+") as f:
                    f.seek(0)
                    json.dump(
                        namespace_aliases, f, sort_keys=True, cls=NumpyEncoder, indent=4
                    )
                    f.truncate()
            if not aliasfile.group() == aliasfile.parent.group():
                shutil.chown(aliasfile, group=aliasfile.parent.group())

            scan.remaining_tasks.append(
                Thread(
                    target=daq.append_aux,
                    args=[aliasfile.resolve().as_posix()],
                    kwargs=dict(pgroup=pgroup, run_number=runno),
                )
            )
            # DEBUG
            print(
                f"Sending scan_info_rel.json in {Path(aliasfile).parent.stem} to run number {runno}."
            )
            scan.remaining_tasks[-1].start()
            # response = daq.append_aux(
            #     aliasfile.resolve().as_posix(),
            #     pgroup=pgroup,
            #     run_number=runno,
            # )
            print("####### transfer aliases started #######")
            # print(response.json())
            # print("################################")
            scan.scan_info["scan_parameters"]["aliases"] = "aux/aliases.json"


def _message_end_scan(scan, **kwargs):
    print(f"Finished run {scan.run_number}.")
    if hasattr(scan, "daq_run_number"):
        runno_daq_saved = scan.daq_run_number
        print(f"daq_run_number is run {runno_daq_saved}.")

    try:
        runno = daq.get_last_run_number()
        print(f"daq last run number is run {runno}.")
    except:
        pass

    try:
        e = pyttsx3.init()
        e.say(f"Finished run {scan.run_number}.")
        e.runAndWait()
        e.stop()
    except:
        print("Audio output failed.")


# def _copy_scan_info_to_raw(scan, daq=daq):
#     run_number = daq.get_last_run_number()
#     pgroup = daq.pgroup
#     print(f"Copying info file to run {run_number} to the raw directory of {pgroup}.")
#     response = daq.append_aux(
#         scan.scan_info_filename, pgroup=pgroup, run_number=run_number
#     )
#     print(f"Status: {response.json()['status']} Message: {response.json()['message']}")


def _create_general_run_info(scan, daq=daq, **kwargs):
    with open(scan.scan_info_filename, "r") as f:
        si = json.load(f)

    info = {}
    # general info, potentially automatically filled
    info["general"] = {}
    # individual data filled by daq/writers/user through api
    info["start"] = {}
    info["end"] = {}
    info["steps"] = []


def _copy_scan_info_to_raw(scan, daq=daq, **kwargs):
    t_start = time.time()

    scan.writeScanInfo()

    # get data that should come later from api or similar.
    run_directory = list(
        Path(f"/sf/bernina/data/{daq.pgroup}/raw").glob(f"run{scan.run_number:04d}*")
    )[0].as_posix()
    # with open(scan.scan_info_filename, "r") as f:
    #     si = json.load(f)
    
    # Get scan info from scan
    si = scan.scan_info

    # correct some data in there (relative paths for now)
    from os.path import relpath

    newfiles = []
    for files in si["scan_files"]:
        newfiles.append([relpath(file, run_directory) for file in files])

    si["scan_files"] = newfiles

    # save temprary file and send then to raw
    if hasattr(scan, "daq_run_number"):
        runno = scan.daq_run_number
    else:
        runno = daq.get_last_run_number()
    pgroup = daq.pgroup
    tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/run_data/daq/run{runno:04d}/aux")
    tmpdir.mkdir(exist_ok=True, parents=True)
    try:
        tmpdir.chmod(0o775)
    except:
        pass
    scaninfofile = tmpdir / Path("scan_info_rel.json")
    if not Path(scaninfofile).exists():
        with open(scaninfofile, "w") as f:
            json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
    else:
        with open(scaninfofile, "r+") as f:
            f.seek(0)
            json.dump(si, f, sort_keys=True, cls=NumpyEncoder, indent=4)
            f.truncate()
    if not scaninfofile.group() == scaninfofile.parent.group():
        shutil.chown(scaninfofile, group=scaninfofile.parent.group())
    # print(f"Copying info file to run {runno} to the raw directory of {pgroup}.")

    scan.remaining_tasks.append(
        Thread(
            target=daq.append_aux,
            args=[scaninfofile.as_posix()],
            kwargs=dict(pgroup=pgroup, run_number=runno),
        )
    )
    # DEBUG
    print(
        f"Sending scan_info_rel.json in {Path(scaninfofile).parent.stem} to run number {runno}."
    )
    scan.remaining_tasks[-1].start()
    # response = daq.append_aux(scaninfofile.as_posix(), pgroup=pgroup, run_number=runno)
    # print(f"Status: {response.json()['status']} Message: {response.json()['message']}")
    # print(
    #     f"--> creating and copying file took{time.time()-t_start} s, presently adding to deadtime."
    # )


from eco.detector import Jungfrau


def _copy_selected_JF_pedestals_to_raw(
    scan, daq=daq, copy_selected_JF_pedestals_to_raw=True, **kwargs
):
    def copy_to_aux(daq, scan):
        if hasattr(scan, "daq_run_number"):
            runno = scan.daq_run_number
        else:
            runno = daq.get_last_run_number()

        pgroup = daq.pgroup

        for jf_id in daq.channels["channels_JF"]():
            jf = Jungfrau(jf_id, name="noname", pgroup_adj=config_bernina.pgroup)
            print(
                f"Copying {jf_id} pedestal to run {runno} in the raw directory of {pgroup}."
            )
            response = daq.append_aux(
                jf.get_present_pedestal_filename_in_run(intempdir=True),
                pgroup=pgroup,
                run_number=runno,
            )
            print(
                f"Status: {response.json()['status']} Message: {response.json()['message']}"
            )
            print(
                f"Copying {jf_id} gainmap to run {runno} in the raw directory of {pgroup}."
            )

            response = daq.append_aux(
                jf.get_present_gain_filename_in_run(intempdir=True),
                pgroup=pgroup,
                run_number=runno,
            )
            print(
                f"Status: {response.json()['status']} Message: {response.json()['message']}"
            )

    if copy_selected_JF_pedestals_to_raw:
        scan.remaining_tasks.append(Thread(target=copy_to_aux, args=[daq, scan]))
        scan.remaining_tasks[-1].start()


def _increment_daq_run_number(scan, daq=daq, **kwargs):
    try:
        daq_last_run_number = daq.get_last_run_number()
        if int(scan.run_number) is int(daq_last_run_number) + 1:
            print("############ incremented ##########")
            daq_run_number = daq.get_next_run_number()
        else:
            daq_run_number = daq_last_run_number
        if int(scan.run_number) is not int(daq_run_number):
            print(
                f"Difference in run number between eco {int(scan.run_number)} and daq {int(daq_run_number)}: using run number {int(scan.run_number)}"
            )
            if int(scan.run_number) > int(daq_run_number):
                n = int(scan.run_number) - int(daq_run_number)
                print("Increasing daq run_number")
                for i in range(n):
                    rn = daq.get_next_run_number()
                    print(rn)
                    scan.daq_run_number = rn
        else:
            scan.daq_run_number = daq_run_number

    except Exception as e:
        print(e)


class Monitor:
    def __init__(self, pvname, start_immediately=True):
        self.data = {}
        self.print = False
        self.pv = PV(pvname)
        self.cb_index = None
        if start_immediately:
            self.start_callback()

    def start_callback(self):
        self.cb_index = self.pv.add_callback(self.append)

    def stop_callback(self):
        self.pv.remove_callback(self.cb_index)

    def append(self, pvname=None, value=None, timestamp=None, **kwargs):
        if not (pvname in self.data):
            self.data[pvname] = []
        ts_local = time.time()
        self.data[pvname].append(
            {"value": value, "timestamp": timestamp, "timestamp_local": ts_local}
        )
        if self.print:
            print(
                f"{pvname}:  {value};  time: {timestamp}; time_local: {ts_local}; diff: {ts_local-timestamp}"
            )


import traceback


def append_scan_monitors(
    scan,
    daq=daq,
    custom_monitors={},
    **kwargs,
):
    scan.monitors = {}
    for adj in scan.adjustables:
        try:
            tname = adj.alias.get_full_name()
        except Exception:
            tname = adj.name
            traceback.print_exc()
        try:
            scan.monitors[tname] = Monitor(adj.pvname)
        except Exception:
            print(f"Could not add CA monitor for {tname}")
            traceback.print_exc()
        try:
            rname = adj.readback.alias.get_full_name()
        except Exception:
            print("no readback configured")
            traceback.print_exc()
        try:
            scan.monitors[rname] = Monitor(adj.readback.pvname)
        except Exception:
            print(f"Could not add CA readback monitor for {tname}")
            traceback.print_exc()

    for tname, tobj in custom_monitors.items():
        try:
            if type(tobj) is str:
                tmonpv = tobj
            scan.monitors[tname] = Monitor(tmonpv)
            print(f"Added custom monitor for {tname}")
        except Exception:
            print(f"Could not add custom monitor for {tname}")
            traceback.print_exc()
    try:
        tname = daq.pulse_id.alias.get_full_name()
        scan.monitors[tname] = Monitor(daq.pulse_id.pvname)
    except Exception:
        print(f"Could not add daq.pulse_id monitor")
        traceback.print_exc()


def end_scan_monitors(scan, daq=daq, **kwargs):
    for tmon in scan.monitors:
        scan.monitors[tmon].stop_callback()

    monitor_result = {tmon: scan.monitors[tmon].data for tmon in scan.monitors}

    #######
    # get data that should come later from api or similar.
    run_directory = list(
        Path(f"/sf/bernina/data/{daq.pgroup}/raw").glob(f"run{scan.run_number:04d}*")
    )[0].as_posix()

    # correct some data in there (relative paths for now)
    from os.path import relpath

    # save temprary file and send then to raw
    if hasattr(scan, "daq_run_number"):
        runno = scan.daq_run_number
    else:
        runno = daq.get_last_run_number()
    pgroup = daq.pgroup
    tmpdir = Path(f"/sf/bernina/data/{pgroup}/res/tmp/info_run{runno:04d}")
    tmpdir.mkdir(exist_ok=True, parents=True)
    try:
        tmpdir.chmod(0o775)
    except:
        pass
    scanmonitorfile = tmpdir / Path("scan_monitor.pkl")
    if not Path(scanmonitorfile).exists():
        with open(scanmonitorfile, "wb") as f:
            pickle.dump(monitor_result, f)

    print(f"Copying monitor file to run {runno} to the raw directory of {pgroup}.")
    response = daq.append_aux(
        scanmonitorfile.as_posix(), pgroup=pgroup, run_number=runno
    )
    print(f"Status: {response.json()['status']} Message: {response.json()['message']}")

    # scan.monitors = None


def _init_all(scan, append_status_info=True, **kwargs):
    if not append_status_info:
        return
    namespace.init_all(silent=False)


callbacks_start_scan = []
callbacks_start_scan.append(_init_all)
callbacks_start_scan.append(_wait_for_tasks)
callbacks_start_scan.append(_append_namesace_status_to_scan)
callbacks_start_scan.append(_increment_daq_run_number)
callbacks_start_scan.append(append_scan_monitors)
callbacks_end_step = []
callbacks_end_step.append(_copy_scan_info_to_raw)
callbacks_end_step.append(_write_namespace_aliases_to_scan)
callbacks_end_step.append(
    lambda scan, daq=daq, namespace=namespace, append_status_info=True, end_scan=True, **kwargs: _write_namespace_status_to_scan(
        scan,
        daq=daq,
        namespace=namespace,
        append_status_info=append_status_info,
        end_scan=False,
        **kwargs,
    )
)
callbacks_end_scan = []
callbacks_end_scan.append(_write_namespace_status_to_scan)
callbacks_end_scan.append(_copy_scan_info_to_raw)
callbacks_end_scan.append(
    lambda scan, daq=daq, force=True, **kwargs: _write_namespace_aliases_to_scan(
        scan, daq=daq, force=force, **kwargs
    )
)
callbacks_end_scan.append(_copy_selected_JF_pedestals_to_raw)
callbacks_end_scan.append(end_scan_monitors)
callbacks_end_scan.append(_message_end_scan)

# >>>> Extract for run_table and elog


# if self._run_table or self._elog:
def _create_metadata_structure_start_scan(
    scan, run_table=run_table, elog=elog, append_status_info=True, **kwargs
):
    runname = os.path.basename(scan.fina).split(".")[0]
    runno = int(runname.split("run")[1].split("_")[0])
    metadata = {
        "type": "scan",
        "name": runname.split("_", 1)[1],
        "scan_info_file": scan.scan_info_filename,
    }
    for n, adj in enumerate(scan.adjustables):
        nname = None
        nId = None
        if hasattr(adj, "Id"):
            nId = adj.Id
        if hasattr(adj, "name"):
            nname = adj.name

        metadata.update(
            {
                f"scan_motor_{n}": nname,
                f"from_motor_{n}": scan.values_todo[0][n],
                f"to_motor_{n}": scan.values_todo[-1][n],
                f"id_motor_{n}": nId,
            }
        )
    if np.mean(np.diff(scan.pulses_per_step)) < 1:
        pulses_per_step = scan.pulses_per_step[0]
    else:
        pulses_per_step = scan.pulses_per_step
    metadata.update(
        {
            "steps": len(scan.values_todo),
            "pulses_per_step": pulses_per_step,
            "counters": [daq.name for daq in scan.counterCallers],
        }
    )

    try:
        try:
            metadata.update({"scan_command": get_ipython().user_ns["In"][-1]})
        except:
            print("Count not retrieve ipython scan command!")

        message_string = f"#### Run {runno}"
        if metadata["name"]:
            message_string += f': {metadata["name"]}\n'
        else:
            message_string += "\n"

        if "scan_command" in metadata.keys():
            message_string += "`" + metadata["scan_command"] + "`\n"
        message_string += "`" + metadata["scan_info_file"] + "`\n"
        elog_ids = elog.post(
            message_string,
            Title=f'Run {runno}: {metadata["name"]}',
            text_encoding="markdown",
        )
        scan._elog_id = elog_ids[1]
        metadata.update({"elog_message_id": scan._elog_id})
        metadata.update(
            {"elog_post_link": scan._elog.elogs[1]._log._url + str(scan._elog_id)}
        )
    except:
        print("Elog posting failed with:")
        traceback.print_exc()
    if not append_status_info:
        return
    d = {}
    ## use values from status for run_table
    try:
        d = scan.status["status_run_start"]["status"]
    except:
        print("Tranferring values from status to run_table did not work")
    t_start_rt = time.time()
    try:
        run_table.append_run(runno, metadata=metadata, d=d)
    except:
        print("WARNING: issue adding data to run table")
    print(f"RT appending: {time.time()-t_start_rt:.3f} s")


# <<<< Extract for run table and elog
callbacks_start_scan.append(_create_metadata_structure_start_scan)