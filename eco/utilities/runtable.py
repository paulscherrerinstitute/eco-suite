from oauth2client.service_account import ServiceAccountCredentials
from pandas import DataFrame
import pandas as pd
import warnings
from ..elements.adjustable import AdjustableFS
from ..elements.memory import Memory
from subprocess import call

warnings.simplefilter(action="ignore", category=pd.errors.PerformanceWarning)
warnings.simplefilter(action="ignore", category=UserWarning)
import timeit
import os
from pathlib import Path
from epics import PV
import numpy as np
import gspread
import gspread_dataframe as gd
import gspread_formatting as gf
import gspread_formatting.dataframe as gf_dataframe
from datetime import datetime
import xlwt
import openpyxl
from ..devices_general.pv_adjustable import PvRecord
from epics import caget
import eco
import threading

pd.options.display.max_rows = 100
pd.options.display.max_columns = 50
pd.options.display.max_colwidth = 50
pd.options.display.width = None
pd.set_option("display.float_format", lambda x: "%.5g" % x)


class Gsheet_API:
    def __init__(
        self,
        keydf_fname,
        cred_fname,
        exp_id,
        exp_path,
        gsheet_key_path,
    ):
        ### credentials and settings for uploading to gspread ###
        self._scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        self._credentials = ServiceAccountCredentials.from_json_keyfile_name(
            "/sf/bernina/config/src/python/gspread/pandas_push", self._scope
        )
        self.gc = gspread.authorize(self._credentials)
        self._keydf_fname = keydf_fname
        self.keys = (
            "metadata gps jet energy las_inc delay lxt pulse_id att_self att_fe_self"
        )
        self._key_df = DataFrame()
        self.gsheet_keys = AdjustableFS(
            gsheet_key_path,
            name="gsheet_keys",
            default_value="metadata thc gps xrd att att_usd kb",
        )
        self.init_runtable(exp_id)

    def create_rt_spreadsheet(self, exp_id):
        self.gc = gspread.authorize(self._credentials)
        spreadsheet = self.gc.create(
            title=f"run_table_{exp_id}", folder_id="1F7DgF0HW1O71nETpfrTvQ35lRZCs5GvH"
        )
        spreadsheet.add_worksheet("runtable", 10, 10)
        spreadsheet.add_worksheet("positions", 10, 10)
        ws = spreadsheet.get_worksheet(0)
        spreadsheet.del_worksheet(ws)
        return spreadsheet

    def _append_to_gspread_key_df(self, gspread_key_df):
        if os.path.exists(self._keydf_fname):
            self._key_df = pd.read_pickle(self._keydf_fname)
            #deprecated: self._key_df = self._key_df.append(gspread_key_df)
            self._key_df = pd.concat([self._key_df, gspread_key_df])
            self._key_df.to_pickle(self._keydf_fname)
        else:
            self._key_df.to_pickle(self._keydf_fname)

    def init_runtable(self, exp_id):
        if os.path.exists(self._keydf_fname):
            self._key_df = pd.read_pickle(self._keydf_fname)
        if self._key_df is not None and str(exp_id) in self._key_df.index:
            spreadsheet_key = self._key_df["keys"][f"{exp_id}"]
        else:
            f_create = str(
                input(
                    f"No google spreadsheet id found for experiment {exp_id}. Create new run_table spreadsheet? (y/n) "
                )
            )
            if f_create == "y":
                print("creating")
                spreadsheet = self.create_rt_spreadsheet(exp_id=exp_id)
                print("created")
                gspread_key_df = DataFrame(
                    {"keys": [spreadsheet.id]},
                    index=[f"{exp_id}"],
                )
                print(gspread_key_df)
                spreadsheet_key = spreadsheet.id
            self._append_to_gspread_key_df(gspread_key_df)
        self._spreadsheet_key = spreadsheet_key

    def upload_rt(self, worksheet="runtable", keys=None, df=None):
        """
        This function uploads all entries of which "type" contains "scan" to the worksheet positions.
        keys takes a string of keys separated by a space, e.g. 'gps xrd las'. All columns, which contain
        any of these strings are uploaded. keys = None defaults to self.keys. keys = '' returns all columns
        """
        self.gc = gspread.authorize(self._credentials)
        ws = self.gc.open_by_key(self._spreadsheet_key).worksheet(worksheet)
        upload_df = df[df["metadata.type"].str.contains("scan", na=False)]
        gd.set_with_dataframe(ws, upload_df, include_index=True, col=2)
        gf_dataframe.format_with_dataframe(
            ws, upload_df, include_index=True, include_column_header=True, col=2
        )

    def upload_pos(self, worksheet="positions", keys=None, df=None):
        """
        This function uploads all entries with "type == pos" to the worksheet positions.
        keys takes a list of strin All columns, which contain any of these strings are uploaded.
        keys = None defaults to self.keys. keys = [] returns all columns
        """
        self.gc = gspread.authorize(self._credentials)
        ws = self.gc.open_by_key(self._spreadsheet_key).worksheet(worksheet)
        upload_df = df[df["metadata.type"].str.contains("pos", na=False)]
        gd.set_with_dataframe(ws, upload_df, include_index=True, col=2)
        gf_dataframe.format_with_dataframe(
            ws, upload_df, include_index=True, include_column_header=True, col=2
        )

    def _upload_all(self, df):
        try:
            self.upload_rt(df=df)
            self.upload_pos(df=df)
        except:
            print(
                f"Uploading of runtable to gsheet https://docs.google.com/spreadsheets/d/{self._spreadsheet_key}/ failed. Run run_table.upload_rt() for error traceback"
            )

    def upload_all(self, df):
        rt = threading.Thread(target=self._upload_all, kwargs={"df": df})
        rt.start()


class Container:
    def __init__(self, df, name=""):
        self._cols = df.columns
        self._top_level_name = name
        self._df = df
        self.__dir__()

    def _slice_df(self):
        self._df.load()
        next_level_names = self._get_next_level_names()
        try:
            if len(next_level_names) == 0:
                columns_to_keep = [self._top_level_name[:-1]]
            else:
                columns_to_keep = [
                    f"{self._top_level_name}{n}"
                    for n in next_level_names
                    if f"{self._top_level_name}{n}" in self._cols
                ]
            sdf = self._df[columns_to_keep]
        except:
            sdf = pd.DataFrame(columns=next_level_names)
        return sdf

    def _get_next_level_names(self):
        if len(self._top_level_name) == 0:
            next_level_names = np.unique(
                np.array([n.split(".")[0] for n in self._cols])
            )
        else:
            next_level_names = np.unique(
                np.array(
                    [
                        n.split(self._top_level_name)[1].split(".")[0]
                        for n in self._cols
                        if n[: len(self._top_level_name)] == self._top_level_name
                    ]
                )
            )
        return next_level_names

    def _create_first_level_container(self, names):
        for n in names:
            self.__dict__[n] = Container(self._df, name=self._top_level_name + n + ".")

    def to_dataframe(self, full_name=True, next_level=False):
        df = self._slice_df()
        if not full_name:
            coln = pd.Index([k.split(self._top_level_name)[1] for k in df.columns])
            df.columns = coln
        return df

    def recall(self, key, next_level=True, get_status=True):
        sr = self[key]
        if next_level:
            srs = [self.__dict__[k][key] for k in self._get_next_level_names()]
            srs.insert(0, sr)
            sr = self._concatenate_srs(srs)
        idxn = pd.Index([k.split(self._top_level_name)[1] for k in sr.index])
        sr.index = idxn
        dev = name2obj(self._df.devices, self._top_level_name)
        memory = Memory(obj=dev, memory_dir="")
        if get_status:
            try:
                # get setting keys from obj
                mem = {
                    tk: {ak: sr[ak] for ak in tv.keys() if ak in sr.index}
                    for tk, tv in dev.get_status().items()
                }
            except:
                mem = {"settings": {k: v for k, v in sr.items() if not "readback" in k}}
        else:
            mem = {"settings": {k: v for k, v in sr.items() if not "readback" in k}}
        memory.recall(input_obj=mem)

    def _concatenate_dfs(self, dfs):
        dfc = dfs[0]
        for df in dfs[1:]:
            dfc = dfc.join(df)
        return dfc

    def _concatenate_srs(self, srs):
        src = srs[0]
        for sr in srs[1:]:
            if type(sr) == pd.core.series.Series:
                src = src.append(sr)
        return src

    def __dir__(self):
        next_level_names = self._get_next_level_names()
        to_create = np.array(
            [n for n in next_level_names if not n in self.__dict__.keys()]
        )
        directory = list(next_level_names)
        directory.extend(["to_dataframe", "recall"])
        self._create_first_level_container(to_create)
        return directory

    def __repr__(self):
        # pd.options.display.width = os.get_terminal_size().columns
        return self._slice_df().T.__repr__()

    def _repr_html_(self):
        sdf = self._slice_df()
        if hasattr(sdf, "_repr_html_"):
            return sdf.T._repr_html_()
        else:
            return None

    def __getitem__(self, key):
        if type(key) is tuple:
            key = list(key)
        df = self._slice_df().loc[key]
        if hasattr(df, "T"):
            df = df.T
        return df


class Run_Table2:
    def __init__(
        self,
        data=None,
        exp_id="no_exp_id",
        exp_path="runtable",
        keydf_fname=None,
        cred_fname=None,
        devices=None,
        name=None,
        gsheet_key_path=None,
    ):

        self._data = Run_Table_DataFrame(
            data=data,
            exp_id=exp_id,
            exp_path=exp_path,
            devices=devices,
            name=name,
        )
        if np.all([k is not None for k in [keydf_fname, cred_fname, gsheet_key_path]]):
            self._google_sheet_api = Gsheet_API(
                keydf_fname,
                cred_fname,
                exp_id,
                exp_path,
                gsheet_key_path,
            )
        else:
            self._google_sheet_api = None
        self.__dir__()

    def append_run(
        self,
        runno,
        metadata,
        d={},
    ):
        self._data.append_run(runno, metadata, d=d)
        if self._google_sheet_api is not None:
            df = self._reduce_df()
            self._google_sheet_api.upload_all(df=df)

    def append_pos(
        self,
        name,
    ):
        self._data.append_pos(name)
        if self._google_sheet_api is not None:
            df = self._reduce_df()
            self._google_sheet_api.upload_all(df=df)

    def to_dataframe(self):
        return DataFrame(self._data)
    ###### diagnostic and convencience functions ######

    def run_table_from_other_pgroup(self, pgroup):
        """
        returns a run_table instance of the specified pgroup
        note: this does neither replace the current run_table nor switch the automatic appending of data to a new run_table or pgroup

        usage: run_table_pxxx = run_table.run_table_from_other_pgroup('pxxx')
        """
        return Run_Table2(data=f'/sf/bernina/data/{pgroup}/res/run_table/{pgroup}_runtable.pkl')

    def check_timeouts(self, include_bad_adjustables=True, plot=True, repeats=1):
        return self._data.check_timeouts(include_bad_adjustables=include_bad_adjustables, plot=plot, repeats=repeats)

    def _reduce_df(
        self,
        keys=None,
    ):
        if keys is None:
            keys = self._google_sheet_api.gsheet_keys()
        dfs = []
        for key in keys.split(" "):
            d = self
            add = True
            for k in key.split("."):
                if k in d.__dict__.keys():
                    d = d.__dict__[k]
                else:
                    add = False
            if all([hasattr(d, "to_dataframe"), add]):
                dfs.append(d.to_dataframe())

        dfc = self._concatenate_dfs(dfs)
        return dfc

    def _create_container(self):
        for n in np.unique(np.array([n.split(".")[0] for n in self._data.columns])):
            self.__dict__[n] = Container(df=self._data, name=n + ".")

    def _concatenate_dfs(self, dfs):
        dfc = dfs[0]
        for df in dfs[1:]:
            dfc = dfc.join(df)
        return dfc

    def __dir__(self):
        devs = np.unique(np.array([n.split(".")[0] for n in self._data.columns]))
        for dev in devs:
            if dev not in self.__dict__.keys():
                self.__dict__[dev] = Container(df=self._data, name=dev + ".")
        directory = self.__dict__.keys()
        return directory

    def __str__(self):
        devs = np.unique(np.array([n.split(".")[0] for n in self._data.columns]))
        devs_abc = np.array([dev[0] for dev in devs])
        devs_dict = {abc: devs[devs_abc == abc] for abc in np.unique(devs_abc)}
        devs_str = ""
        for key, value in devs_dict.items():
            devs_str = devs_str + f"{key.capitalize()}\n"
            for val in value:
                devs_str = devs_str + f"{val}\n"
            devs_str = devs_str + f"\n"
        return devs_str

    def __repr__(self):
        return self.__str__()

    def check_timeouts(self, include_bad_adjustables=True, plot=True, repeats=1):
        return self._data.check_timeouts(include_bad_adjustables=include_bad_adjustables, plot=plot, repeats=repeats)


class Run_Table_DataFrame(DataFrame):
    def __init__(
        self,
        data=None,
        exp_id=None,
        exp_path=None,
        devices=None,
        name=None,
    ):
        if type(data) is str:
            data = pd.read_pickle(data)
        super().__init__(data=data)

        ### Load devices to parse for adjustables ###
        if devices is not None:
            devices = eco.__dict__[devices]
        self.devices = devices
        self.name = name
        self.fname = exp_path + f"{exp_id}_runtable.pkl"
        self.load()

        ### dicts holding adjustables and bad (not connected) adjustables ###
        self.adjustables = {}
        self.bad_adjustables = {}

        ###parsing options
        self._parse_exclude_keys = "status_indicators settings_collection status_indicators_collection presets memory _elog _currentChange _flags __ alias namespace daq scan MasterEventSystem _motor Alias".split(
            " "
        )
        self._parse_exclude_class_types = "__ alias namespace daq scan MasterEventSystem _motor Alias AdjustablePv Collection".split(
            " "
        )
        self._adj_exclude_class_types = (
            "__ alias namespace daq scan MasterEventSystem _motor Alias".split(" ")
        )
        self.key_order = "metadata gps xrd midir env_thc temperature1_rbk temperature2_rbk  time name gps gps_hex thc ocb eos las lxt phase_shifter mono att att_fe slit_und slit_switch slit_att slit_kb slit_cleanup pulse_id mono_energy_rbk att_transmission att_fe_transmission"
        pd.options.display.max_rows = 100
        pd.options.display.max_columns = 50
        pd.set_option("display.float_format", lambda x: "%.5g" % x)

    # def _get_values(self):
    #    is_connected = np.array([pv.connected for pv in self._pvs.values()])
    #    filtered_dict = {key: pv.value for key, pv in self._pvs.items() if pv.connected}
    #    return filtered_dict

    @property
    def df(self):
        return self

    @df.setter
    def df(self, data):
        super().__init__(data)

    @df.deleter
    def df(self):
        return

    def _remove_duplicates(self):
        self.df = self[~self.index.duplicated(keep="last")]

    def save(self):
        data_dir = Path(os.path.dirname(self.fname))
        if not data_dir.exists():
            print(
                f"Path {data_dir.absolute().as_posix()} does not exist, will create it..."
            )
            data_dir.mkdir(parents=True)
            print(f"Tried to create {data_dir.absolute().as_posix()}")
            data_dir.chmod(0o775)
            print(f"Tried to change permissions to 775")
        pd.DataFrame(self).to_pickle(self.fname + "tmp")
        call(["mv", self.fname + "tmp", self.fname])

    def load(self):
        if os.path.exists(self.fname):
            self.df = pd.read_pickle(self.fname)

    def append_run(
        self,
        runno,
        metadata={
            "type": "ascan",
            "name": "phi scan (001)",
            "scan_motor": "phi",
            "from": 1,
            "to": 2,
            "steps": 51,
        },
        d={},
    ):
        self.load()
        if len(self.adjustables) == 0:
            self._parse_parent_fewerparents()
        dat = self._get_adjustable_values(d=d)
        dat["metadata"] = metadata
        dat["metadata"]["time"] = datetime.now()
        names = ["device", "adjustable"]
        multiindex = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names
        )
        values = np.array([val for adjs in dat.values() for val in adjs.values()], dtype=object)
        index = np.array(
            [f"{dev}.{adj}" for dev, adjs in dat.items() for adj in adjs.keys()]
        )
        # run_df = DataFrame([values], columns=multiindex, index=[runno])
        run_df = DataFrame([values], columns=index, index=[runno])
        #deprecated: self.df = self.append(run_df)
        self.df = pd.concat([self.df, run_df])

        self._remove_duplicates()
        # self.order_df()
        self.save()

    def append_pos(self, name=""):
        self.load()
        if len(self.adjustables) == 0:
            self._parse_parent_fewerparents()
        try:
            posno = (
                int(self[self["metadata.type"] == "pos"].index[-1].split("p")[1]) + 1
            )
        except:
            posno = 0
        dat = self._get_adjustable_values()
        dat["metadata"] = {"time": datetime.now(), "name": name, "type": "pos"}
        names = ["device", "adjustable"]
        multiindex = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names
        )
        values = np.array([val for adjs in dat.values() for val in adjs.values()], dtype=object)
        index = np.array(
            [f"{dev}.{adj}" for dev, adjs in dat.items() for adj in adjs.keys()]
        )
        # pos_df = DataFrame([values], columns=multiindex, index=[f"p{posno}"])
        pos_df = DataFrame([values], columns=index, index=[f"p{posno}"])

        #deprecated: self.df = self.append(pos_df)
        self.df = pd.concat([self.df,pos_df])
        self._remove_duplicates()
        # self.order_df()
        self.save()

    def _get_adjustable_values(self, silent=True, d={}):
        """
        This function gets the values of all adjustables in good adjustables and raises an error, when an adjustable is not connected anymore
        """
        if silent:
            dat = {}
            for devname, dev in self.good_adjustables.items():
                dat[devname] = {}
                bad_adjs = []
                for adjname, adj in dev.items():
                    if f'{devname}.{adjname}' in d.keys():
                        dat[devname][adjname] = d[f'{devname}.{adjname}']
                        continue
                    try:
                        dat[devname][adjname] = adj.get_current_value()
                    except:
                        print(
                            f"run_table: getting value of {devname}.{adjname} failed, removing it from list of good adjustables"
                        )
                        bad_adjs.append(adjname)
                for ba in bad_adjs:
                    if not devname in self.bad_adjustables.keys():
                        self.bad_adjustables[devname] = {}
                    self.bad_adjustables[devname][ba] = self.good_adjustables[
                        devname
                    ].pop(ba)
        else:
            dat = {
                devname: {
                    adjname: d[f'{devname}.{adjname}'] if f'{devname}.{adjname}' in d.keys() else adj.get_current_value() for adjname, adj in dev.items()
                }
                for devname, dev in self.good_adjustables.items()
            }
        return dat

    def _get_all_adjustables(self, device, pp_name=None):
        if pp_name is not None:
            name = ".".join([pp_name, device.name])
        else:
            name = device.name
        self.adjustables[name] = {}
        for key in device.__dict__.keys():
            if ~np.any([s in key for s in self._parse_exclude_keys]):
                value = device.__dict__[key]
                if np.all(
                    [
                        ~np.any(
                            [
                                s in str(type(value))
                                for s in self._adj_exclude_class_types
                            ]
                        ),
                        hasattr(value, "get_current_value"),
                    ]
                ):
                    self.adjustables[name][key] = value

        if hasattr(device, "get_current_value"):
            self.adjustables[name][".".join([name, "self"])] = device

    def _get_all_adjustables_fewerparents(
        self, device, adj_prefix=None, parent_name=None, verbose=False
    ):
        if adj_prefix is not None:
            name = ".".join([adj_prefix, device.name])
        else:
            name = device.name
        for key in device.__dict__.keys():
            if ~np.any([s in key for s in self._parse_exclude_keys]):
                value = device.__dict__[key]
                if np.all(
                    [
                        ~np.any(
                            [
                                s in str(type(value))
                                for s in self._adj_exclude_class_types
                            ]
                        ),
                        hasattr(value, "get_current_value"),
                    ]
                ):
                    if parent_name == device.name:
                        self.adjustables[parent_name][key] = value
                    else:
                        #print("GET ADJ", parent_name, name, key)
                        
                        self.adjustables[parent_name][".".join([name, key])] = value

        if parent_name == device.name:
            if hasattr(device, "get_current_value"):
                self.adjustables[parent_name]["self"] = device

    def _parse_child_instances_fewerparents(
        self, parent_class, adj_prefix=None, parent_name=None, verbose=False
    ):
        if parent_name is None:
            parent_name = own_name
        self._get_all_adjustables_fewerparents(parent_class, adj_prefix, parent_name, verbose=verbose)
        if parent_name is not parent_class.name:
            if adj_prefix is not None:
                adj_prefix = ".".join([adj_prefix, parent_class.name])
            else:
                adj_prefix = parent_class.name


        sub_classes = []
        sub_classnames = []
        for key in parent_class.__dict__.keys():
            if ~np.any([s in key for s in self._parse_exclude_keys]):
                s_class = parent_class.__dict__[key]

                if np.all(
                    [
                        hasattr(s_class, "name"),
                        hasattr(s_class, "__dict__"),
                        s_class.__hash__ is not None,
                        "eco" in str(s_class.__class__),
                        ~np.any(
                            [
                                s in str(s_class.__class__)
                                for s in self._parse_exclude_class_types
                            ]
                        ),

                    ]
                ):
                    if adj_prefix is None or ~np.any([key == s for s in ".".join([parent_name,adj_prefix]).split(".")]):
                        if s_class.name == None:
                            s_class.name = key
                        sub_classes.append(s_class)
        return set(sub_classes).union(
            [
                s
                for c in sub_classes
                for s in self._parse_child_instances_fewerparents(
                    c, adj_prefix, parent_name
                )
            ]
        )

    def _parse_parent_fewerparents(self, parent=None, verbose=False):
        if parent == None:
            parent = self.devices
        for key in parent.__dict__.keys():
            try:
                if ~np.any([s in key for s in self._parse_exclude_keys]):
                    s_class = parent.__dict__[key]
                    if np.all(
                        [
                            hasattr(s_class, "name"),
                            hasattr(s_class, "__dict__"),
                            s_class.__hash__ is not None,
                            "eco" in str(s_class.__class__),
                            ~np.any(
                                [
                                    s in str(s_class.__class__)
                                    for s in self._parse_exclude_class_types
                                ]
                            ),
                        ]
                    ):
                        self.adjustables[key] = {}
                        self._parse_child_instances_fewerparents(
                            s_class, parent_name=key, verbose=verbose
                        )
            except Exception as e:
                print(e)
                print(key)
                # print(f"failed to parse {key} in runtable")
        self._check_adjustables()

    def _parse_child_instances(self, parent_class, pp_name=None):
        # try:
        self._get_all_adjustables(parent_class, pp_name)
        # except:
        #    print(f'Getting adjustables from {parent_class.name} failed')
        #    pass
        if pp_name is not None:
            pp_name = ".".join([pp_name, parent_class.name])
        else:
            pp_name = parent_class.name

        sub_classes = []
        for key in parent_class.__dict__.keys():
            if ~np.any([s in key for s in self._parse_exclude_keys]):
                s_class = parent_class.__dict__[key]
                if np.all(
                    [
                        hasattr(s_class, "__dict__"),
                        hasattr(s_class, "name"),
                        s_class.__hash__ is not None,
                        "eco" in str(type(s_class)),
                        ~np.any(
                            [
                                s in str(type(s_class))
                                for s in self._parse_exclude_class_types
                            ]
                        ),
                    ]
                ):
                    sub_classes.append(s_class)
        return set(sub_classes).union(
            [s for c in sub_classes for s in self._parse_child_instances(c, pp_name)]
        )

    def _parse_parent(self, parent=None):
        if parent == None:
            parent = self.devices
        for key in parent.__dict__.keys():
            try:
                if ~np.any([s in key for s in self._parse_exclude_keys]):
                    s_class = parent.__dict__[key]
                    if np.all(
                        [
                            hasattr(s_class, "__dict__"),
                            hasattr(s_class, "name"),
                            s_class.__hash__ is not None,
                            "eco" in str(type(s_class)),
                            ~np.any(
                                [
                                    s in str(type(s_class))
                                    for s in self._parse_exclude_class_types
                                ]
                            ),
                        ]
                    ):
                        self._parse_child_instances(parent.__dict__[key])
            except Exception as e:
                print(e)
                print(key)
                # print(f"failed to parse {key} in runtable")

        self._check_adjustables()

    def _check_adjustables(self, check_for_current_none_values=True):
        good_adj = {}
        bad_adj = {}
        for device, adjs in self.adjustables.items():
            good_dev_adj = {}
            bad_dev_adj = {}
            for name, adj in adjs.items():
                try:
                    adj.get_current_value()
                except Exception as e:
                    print(f"get_current_value() method of {name} failed with {e}")
                    continue
                if check_for_current_none_values and (adj.get_current_value() is None):
                    bad_dev_adj[name] = adj
                else:
                    good_dev_adj[name] = adj
            if len(good_dev_adj) > 0:
                good_adj[device] = good_dev_adj
            if len(bad_dev_adj) > 0:
                bad_adj[device] = bad_dev_adj
            self.good_adjustables = good_adj
            self.bad_adjustables = bad_adj

    def _orderlist(self, mylist, key_order, orderlist=None):
        key_order = key_order.split(" ")
        if orderlist == None:
            index = np.concatenate(
                [np.where(np.array(mylist) == k)[0] for k in key_order if k in mylist]
            )
        else:
            index = np.concatenate(
                [
                    np.where(np.array(orderlist) == k)[0]
                    for k in key_order
                    if k in orderlist
                ]
            )
        curidx = np.arange(len(mylist))
        newidx = np.append(index, np.delete(curidx, index))
        return [mylist[n] for n in newidx]

    def order_df(self, key_order=None):
        """
        This function orders the columns of the stored dataframe by the given key_order.
        key_order is a string with consecutive keys such as 'name type pulse_id. It defaults to self.key_order'
        """
        if key_order is None:
            key_order = self.key_order
        devs = [item[0] for item in list(self.columns)]
        self.df = self[self._orderlist(list(self.columns), key_order, orderlist=devs)]


    #### diagnostic and convenience functions ####
    def check_timeouts(self, include_bad_adjustables=True, repeats=1, plot=True):
        if len(self.adjustables) == 0:
            self._parse_parent_fewerparents()
        ts = []
        devs=[]
        def get_dev_adjs(dev):
            for k, adj in dev.items():
                val = adj.get_current_value()
        for k, dev in self.good_adjustables.items():
            def func(dev=dev):
                return get_dev_adjs(dev)
            t = timeit.timeit(func, number=repeats)
            ts.append(float(t))
            devs.append(k)
            print(k, t)
        idx = np.argsort(ts)
        self.times = [np.array(devs)[idx], np.array(ts)[idx]]
        print('recorded adjustable results stored in run_table._data.times')
        if include_bad_adjustables:
            for k, dev in self.bad_adjustables.items():
                def func(dev=dev):
                    return get_dev_adjs(dev)
                t = timeit.timeit(func, number=repeats)
                ts.append(float(t))
                devs.append(k)
                print(k, t)
            idx = np.argsort(ts)
            print('rejected timed out adjustable results stored in run_table._data.times_rejected')
            self.times_rejected = [np.array(devs)[idx], np.array(ts)[idx]]

        if plot:
            import pylab as plt
            fig, ax = plt.subplots(1)
            if include_bad_adjustables:
                plt.barh(self.times_rejected[0], self.times_rejected[1], color='red', label='rejected adjustables')
            plt.barh(self.times[0], self.times[1], label='recorded adjustables', color='seagreen')
            plt.xlabel('time (s)')
            plt.legend()



def name2obj(obj_parent, name, delimiter="."):
    if type(name) is str:
        name = name.split(delimiter)
    obj = obj_parent
    for tn in name:
        if not tn or tn == "self":
            obj = obj
        else:
            obj = obj.__dict__[tn]

    return obj
