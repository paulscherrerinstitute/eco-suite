from oauth2client.service_account import ServiceAccountCredentials
from pandas import DataFrame
import pandas as pd
import warnings
from ..elements.adjustable import AdjustableFS

warnings.simplefilter(action="ignore", category=pd.errors.PerformanceWarning)
warnings.simplefilter(action="ignore", category=UserWarning)

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

class Run_Table:
    def __init__(
        self,
        pgroup=None,
        devices=None,
        channels_ca={"pulse_id": "SLAAR11-LTIM01-EVR0:RX-PULSEID"},
        name=None,
    ):

        ### Load device and alias_namespace after init of other devices ###
        devices = eco.__dict__[devices]
        self.devices = devices
        self.name = name
        self.adj_df = DataFrame()
        self.unit_df = DataFrame()
        self.gspread_key_df = None
        self.gspread_key_file_name = (
            f"/sf/bernina/config/src/python/gspread/gspread_keys"
        )

        self._channels_ca = channels_ca

        ### credentials and settings for uploading to gspread ###
        self._scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        self._credentials = ServiceAccountCredentials.from_json_keyfile_name(
            "/sf/bernina/config/src/python/gspread/pandas_push", self._scope
        )
        self.gc = gspread.authorize(self._credentials)
        self.keys = "metadata midir xrd energy transmission delay lxt pulse_id att_self att_fe_self"
        self.key_order = "metadata xrd midir env_thc temperature1_rbk temperature2_rbk  time name gps gps_hex thc ocb eos las lxt phase_shifter mono att att_fe slit_und slit_switch slit_att slit_kb slit_cleanup pulse_id mono_energy_rbk att_transmission att_fe_transmission"
        spreadsheet_key=None,
        self.init_runtable(pgroup)

        ### dicts holding adjustables and bad (not connected) adjustables ###
        self.adjustables = {}
        self.bad_adjustables = {}
        self.units = {}

        ###parsing options
        self._parse_exclude_keys = "status_indicators settings_collection status_indicators_collection presets memory _elog _currentChange _flags __ alias namespace daq scan evr _motor Alias".split(
            " "
        )
        self._parse_exclude_class_types = (
            "__ alias namespace daq scan evr _motor Alias AdjustablePv AxisPTZ".split(
                " "
            )
        )
        self._adj_exclude_class_types = (
            "__ alias namespace daq scan evr _motor Alias".split(" ")
        )

        pd.options.display.max_rows = 100
        pd.options.display.max_columns = 50
        pd.set_option("display.float_format", lambda x: "%.5g" % x)

    def create_rt_spreadsheet(self, pgroup):
        self.gc = gspread.authorize(self._credentials)
        spreadsheet = self.gc.create(
            title=f"run_table_{pgroup}", folder_id="1F7DgF0HW1O71nETpfrTvQ35lRZCs5GvH"
        )
        spreadsheet.add_worksheet("runtable", 10, 10)
        spreadsheet.add_worksheet("positions", 10, 10)
        ws = spreadsheet.get_worksheet(0)
        spreadsheet.del_worksheet(ws)
        return spreadsheet

    def _append_to_gspread_key_df(self, gspread_key_df):
        if os.path.exists(self.gspread_key_file_name + ".pkl"):
            self.gspread_key_df = pd.read_pickle(self.gspread_key_file_name + ".pkl")
            self.gspread_key_df = self.gspread_key_df.append(gspread_key_df)
            self.gspread_key_df.to_pickle(self.gspread_key_file_name + ".pkl")
        else:
            self.gspread_key_df.to_pickle(self.gspread_key_file_name + ".pkl")

    def init_runtable(self, pgroup):
        if os.path.exists(self.gspread_key_file_name + ".pkl"):
            self.gspread_key_df = pd.read_pickle(self.gspread_key_file_name + ".pkl")
        if self.gspread_key_df is not None and str(pgroup) in self.gspread_key_df.index:
            spreadsheet_key = self.gspread_key_df["keys"][f"{pgroup}"]
        else:
            f_create = str(
                input(
                    f"No google spreadsheet id found for pgroup {pgroup}. Create new run_table spreadsheet? (y/n) "
                )
            )
            if f_create == "y":
                print("creating")
                spreadsheet = self.create_rt_spreadsheet(pgroup=pgroup)
                print("created")
                gspread_key_df = DataFrame(
                    {"keys": [spreadsheet.id]}, index=[f"{pgroup}"]
                )
                spreadsheet_key = spreadsheet.id
            else:
                f_entermanually = input(f"Do you want to enter a spreadsheet key for the pgroup {pgroup}? (y/n)")
                if f_entermanually is not 'y':
                    print('Runtable not initialized')
                    return
                spreadsheet_key = str(
                    input(
                        f"Please enter the google spreadsheet key, e.g. 1gK--KePLpYCs7U3QfNSPo69XipndbINe1Iz8to9bY1U: "
                    )
                )
                gspread_key_df = DataFrame(
                    {"keys": [spreadsheet_key]}, index=[f"{pgroup}"]
                )
            self._append_to_gspread_key_df(gspread_key_df)
        self._spreadsheet_key = spreadsheet_key
        #self.alias_file_name = (
        #    f"/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_alias_runtable"
        #)
        self.adj_file_name = (
            f"/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_adjustable_runtable"
        )
        self.unit_file_name = (
            f"/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_unit_runtable"
        )
        self.load()
        return

    def _query_by_keys(self, keys="", df=None):
        if df is None:
            df = self.adj_df
        keys = keys.split(" ")
        if len(df.columns[0]) > 1:
            query_df = df[
                df.columns[
                    np.array(
                        [
                            np.any([np.any([x in i for x in keys]) for i in col])
                            for col in df.columns
                        ]
                    )
                ]
            ]
        else:
            query_df = df[
                df.columns[
                    np.array([np.any([x in col for x in keys]) for col in df.columns])
                ]
            ]
        return query_df

    def query(self, keys="", index=None, values=None, df=None):
        """
        function to show saved data. keys is a string with keys separated by a space.
        All columns, which contain any of these strings are returned.            self.prefix
            + f"{runno:{self.Ndigits}0d}"
            + self.separator
            + "*."
            + self.suffix
        Index can be a list od  indices.

        example: query(keys='xrd delay name', index = [0,5])
        will return all columns containing either xrd or delay and  show the data for runs 0 and 5

        example 2: query(keys = 'xrd delay name', index = ['p1', 'p2'])
        will return the same columns for the saved positions 1 and 2
        """
        self.load()
        # if len(keys) > 0:
        #    keys += " name"
        query_df = self._query_by_keys(keys, df)
        if not values is None:
            query_df = query_df.query(values)
        query_df = query_df.T
        if not index is None:
            query_df = query_df[index]
        return query_df

    def _get_values(self):
        is_connected = np.array([pv.connected for pv in self._pvs.values()])
        filtered_dict = {key: pv.value for key, pv in self._pvs.items() if pv.connected}
        return filtered_dict

    def _remove_duplicates(self):
        self.adj_df = self.adj_df[~self.adj_df.index.duplicated(keep="last")]
        #self.alias_df = self.alias_df[~self.alias_df.index.duplicated(keep="last")]
        self.unit_df = self.unit_df[~self.unit_df.index.duplicated(keep="last")]

    def save(self):
        data_dir = Path(os.path.dirname(self.adj_file_name + ".pkl"))
        if not data_dir.exists():
            print(
                f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
            )
            data_dir.mkdir(parents=True)
            print(f"Tried to create {data_dir.absolute().as_posix()}")
            data_dir.chmod(0o775)
            print(f"Tried to change permissions to 775")
        #self.alias_df.to_pickle(self.alias_file_name + ".pkl")
        self.adj_df.to_pickle(self.adj_file_name + ".pkl")
        self.unit_df.to_pickle(self.unit_file_name + ".pkl")

    def load(self):
        #if os.path.exists(self.alias_file_name + ".pkl"):
        #    self.alias_df = pd.read_pickle(self.alias_file_name + ".pkl")
        if os.path.exists(self.adj_file_name + ".pkl"):
            self.adj_df = pd.read_pickle(self.adj_file_name + ".pkl")
        if os.path.exists(self.unit_file_name + ".pkl"):
            self.unit_df = pd.read_pickle(self.unit_file_name + ".pkl")

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
    ):
        self.load()
        if len(self.adjustables) == 0:
            self._parse_parent_fewerparents()
        #dat = self._get_values()
        #dat.update(metadata)
        #dat["time"] = datetime.now()
        #run_df = DataFrame([dat.values()], columns=dat.keys(), index=[runno])
        #self.alias_df = self.alias_df.append(run_df)

        dat = self._get_adjustable_values()
        dat["metadata"] = metadata
        dat["metadata"]["time"] = datetime.now()
        names = ["device", "adjustable"]
        multiindex = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names
        )
        values = np.array([val for adjs in dat.values() for val in adjs.values()])
        run_df = DataFrame([values], columns=multiindex, index=[runno])
        self.adj_df = self.adj_df.append(run_df)
        multiindex_u = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in self.units.keys() for adj in self.units[dev].keys()],
            names=names,
        )
        values_u = np.array(
            [val for adjs in self.units.values() for val in adjs.values()]
        )
        self.unit_df = DataFrame([values_u], columns=multiindex_u, index=["units"])
        self._remove_duplicates()
        self.save()
        self.upload_all()

    def append_pos(self, name=""):
        self.load()
        if len(self.adjustables) == 0:
            self._parse_parent_fewerparents()
        try:
            posno = (
                int(self.adj_df.query('type == "pos"').index[-1].split("p")[1]) + 1
            )
        except:
            posno = 0
        #dat = self._get_values()
        #dat.update([("name", name), ("type", "pos")])
        #dat["time"] = datetime.now()
        #pos_df = DataFrame([dat.values()], columns=dat.keys(), index=[f"p{posno}"])
        #self.alias_df = self.alias_df.append(pos_df)

        dat = self._get_adjustable_values()
        dat["metadata"] = {"time": datetime.now(), "name": name, "type": "pos"}
        names = ["device", "adjustable"]
        multiindex = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names
        )
        values = np.array([val for adjs in dat.values() for val in adjs.values()])
        pos_df = DataFrame([values], columns=multiindex, index=[f"p{posno}"])
        self.adj_df = self.adj_df.append(pos_df)
        multiindex_u = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in self.units.keys() for adj in self.units[dev].keys()],
            names=names,
        )
        values_u = np.array(
            [val for adjs in self.units.values() for val in adjs.values()]
        )
        self.unit_df = DataFrame([values_u], columns=multiindex_u, index=["units"])
        self.save()
        self.upload_all()

    def upload_rt(self, worksheet="runtable", keys=None, df=None):
        """
        This function uploads all entries of which "type" contains "scan" to the worksheet positions.
        keys takes a string of keys separated by a space, e.g. 'gps xrd las'. All columns, which contain
        any of these strings are uploaded. keys = None defaults to self.keys. keys = '' returns all columns
        """
        self.load()
        self.gc = gspread.authorize(self._credentials)
        self.order_df()
        if keys is None:
            keys = self.keys

        self.ws = self.gc.open_by_key(self._spreadsheet_key).worksheet(worksheet)
        if len(keys) > 0:
            keys = keys + " type"
            upload_df = self._query_by_keys(keys=keys, df=df)
        else:
            upload_df = df
            if df is None:
                upload_df = self.adj_df
        upload_df = upload_df[
            upload_df["metadata"]["type"].str.contains("scan", na=False)
        ]
        gd.set_with_dataframe(self.ws, upload_df, include_index=True, col=2)
        gf_dataframe.format_with_dataframe(
            self.ws, upload_df, include_index=True, include_column_header=True, col=2
        )

    def upload_pos(self, worksheet="positions", keys=None):
        """
        This function uploads all entries with "type == pos" to the worksheet positions.
        keys takes a list of strin All columns, which contain any of these strings are uploaded.
        keys = None defaults to self.keys. keys = [] returns all columns
        """
        self.load()
        self.gc = gspread.authorize(self._credentials)
        self.order_df()
        if keys is None:
            keys = self.keys

        self.ws = self.gc.open_by_key(self._spreadsheet_key).worksheet(worksheet)
        if len(keys) > 0:
            keys = keys + " metadata"
            upload_df = self._query_by_keys(keys=keys)
        else:
            upload_df = self.adj_df
        upload_df = upload_df[
            upload_df["metadata"]["type"].str.contains("pos", na=False)
        ]
        gd.set_with_dataframe(self.ws, upload_df, include_index=True, col=2)
        gf_dataframe.format_with_dataframe(
            self.ws, upload_df, include_index=True, include_column_header=True, col=2
        )

    def _upload_all(self):
        try:
            self.upload_rt()
            self.upload_pos()
        except:
            print(
                f"Uploading of runtable to gsheet https://docs.google.com/spreadsheets/d/{self._spreadsheet_key}/ failed. Run run_table.upload_rt() for error traceback"
            )

    def upload_all(self):
        rt = threading.Thread(target=self._upload_all)
        rt.start()

    def _orderlist(self, mylist, key_order, orderlist=None):
        key_order = key_order.split(" ")
        if orderlist == None:
            index = np.concatenate(
                [np.where(np.array(mylist) == k)[0] for k in key_order if k in mylist]
            )
            # index = np.array([mylist.index(k) for k in key_order if k in mylist])
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
        #self.alias_df = self.alias_df[
        #    self._orderlist(list(self.alias_df.columns), key_order)
        #]
        devs = [item[0] for item in list(self.adj_df.columns)]
        self.adj_df = self.adj_df[
            self._orderlist(list(self.adj_df.columns), key_order, orderlist=devs)
        ]

    def _get_adjustable_values(self, silent=True):
        """
        This function gets the values of all adjustables in good adjustables and raises an error, when an adjustable is not connected anymore
        """
        if silent:
            dat = {}
            for devname, dev in self.good_adjustables.items():
                dat[devname] = {}
                for adjname, adj in dev.items():
                    bad_adjs = []
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
                    self.bad_adjustables[devname][adjname] = self.good_adjustables[
                        devname
                    ].pop(adjname)
        else:
            dat = {
                devname: {
                    adjname: adj.get_current_value() for adjname, adj in dev.items()
                }
                for devname, dev in self.good_adjustables.items()
            }
        return dat

    def subtract_df(self, devs, ind1, ind2):
        """
        This function is used to subtract one dataframe from another to show changes between entries.
        devs='thc tht' would show the devices thc and tht and ind1=0, ind2='p0' the difference between
        run 0 and saved position 0.
        """
        df1 = self.query(devs, [ind1])
        df1 = df1[[type(val) is not str for val in df1]]
        df2 = self.query(devs, [ind2])
        df2 = df2[[type(val) is not str for val in df2]]
        df2.columns = df1.columns
        return df1.subtract(df2)

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
        self, device, adj_prefix=None, parent_name=None
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
                        self.adjustables[parent_name][".".join([name, key])] = value

        if parent_name == device.name:
            if hasattr(device, "get_current_value"):
                self.adjustables[parent_name]["self"] = device

    def _parse_child_instances_fewerparents(
        self, parent_class, adj_prefix=None, parent_name=None
    ):
        if parent_name is None:
            parent_name = parent_class.name
        self._get_all_adjustables_fewerparents(parent_class, adj_prefix, parent_name)
        if parent_name is not parent_class.name:
            if adj_prefix is not None:
                adj_prefix = ".".join([adj_prefix, parent_class.name])
            else:
                adj_prefix = parent_class.name

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
            [
                s
                for c in sub_classes
                for s in self._parse_child_instances_fewerparents(
                    c, adj_prefix, parent_name
                )
            ]
        )

    def _parse_parent_fewerparents(self, parent=None):
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
                        self.adjustables[s_class.name] = {}
                        self._parse_child_instances_fewerparents(s_class)
            except Exception as e:
                print(e)
                print(key)
                # print(f"failed to parse {key} in runtable")
        for name, value in self._channels_ca.get_current_value().items():
            self.adjustables[f"env_{name}"] = {
                key: PvRecord(pvsetname=ch) for key, ch in value.items()
            }
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
        for name, value in self._channels_ca.get_current_value().items():
            self.adjustables[f"env_{name}"] = {
                key: PvRecord(pvsetname=ch) for key, ch in value.items()
            }
        self._check_adjustables()

    def _check_adjustables(self, check_for_current_none_values=False):
        good_adj = {}
        bad_adj = {}
        for device, adjs in self.adjustables.items():
            good_dev_adj = {}
            bad_dev_adj = {}
            for name, adj in adjs.items():
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

    def set_alias_namespace(self, alias_namespace):
        aliases = [s.replace(".", "_") for s in alias_namespace.aliases]
        self._alias_namespace = alias_namespace
        self._pvs = dict(
            zip(
                aliases,
                np.array(
                    [
                        PV(ch, connection_timeout=0.05, auto_monitor=True)
                        for ch in alias_namespace.channels
                    ]
                ),
            )
        )

    def get_alias_namespace(self):
        return self._alias_namespace

    alias_namespace = property(get_alias_namespace, set_alias_namespace)

    def __repr__(self):
        self.order_df()
        return_df = self._query_by_keys(self.keys)
        return return_df.T.__repr__()

class Gsheet_API:
    def __init__(
        self,
        keydf_fname,
        cred_fname,
        exp_id,
        exp_path,
    ):
        ### credentials and settings for uploading to gspread ###
        self._scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        self._credentials = ServiceAccountCredentials.from_json_keyfile_name(
            cred_fname, 
            self._scope
        )
        self._keydf_fname = keydf_fname
        self.keys = "metadata midir xrd energy transmission delay lxt pulse_id att_self att_fe_self"
        self._key_df=DataFrame()
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
            self._key_df = self._key_df.append(gspread_key_df)
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
                    {"keys": [spreadsheet.id]}, index=[f"{exp_id}"],
                )
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
        upload_df = df[
            df["metadata.type"].str.contains("scan", na=False)
        ]
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
        upload_df = df[
            df["metadata.type"].str.contains("pos", na=False)
        ]
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
        rt = threading.Thread(target=self._upload_all,  kwargs={'df':df})
        rt.start()

class Container:
    def __init__(self, df, name=''):
        self._cols = df.columns
        self._top_level_name = name
        self._df = df
        self.__dir__()
    
    def _slice_df(self):
        next_level_names = self._get_next_level_names()
        try:
            if len(next_level_names)==0:
                sdf = self._df[self._top_level_name[:-1]]
            else:
                columns_to_keep = [f'{self._top_level_name}{n}' for n in next_level_names if f'{self._top_level_name}{n}' in self._cols]
                sdf = self._df[columns_to_keep]
        except:
            sdf = pd.DataFrame(columns=next_level_names)
        return sdf
    
    def _get_next_level_names(self):
        if len(self._top_level_name) == 0:
            next_level_names = np.unique(np.array([n.split('.')[0] for n in self._cols]))
        else:
            next_level_names = np.unique(np.array([n.split(self._top_level_name)[1].split('.')[0] for n in self._cols if len(n.split(self._top_level_name))>1]))
        return next_level_names

    def _create_first_level_container(self, names):
        for n in names:
            self.__dict__[n]=Container(self._df, name=self._top_level_name+n+'.')

    def to_dataframe(self):
        return self._slice_df()
    
    def __dir__(self):
        next_level_names = self._get_next_level_names()
        to_create = np.array([n for n in next_level_names if not n in self.__dict__.keys()])
        directory = list(next_level_names)
        directory.append('to_dataframe')
        self._create_first_level_container(to_create)
        return directory

    def __repr__(self):
        return self._slice_df().T.__repr__()

    def _repr_html_(self):
        sdf = self._slice_df()
        if hasattr(sdf, '_repr_html_'):
            return sdf.T._repr_html_()
        else:
            return None

    def __getitem__(self, key):
        return self._slice_df().loc[key]

class Run_Table2:
    def __init__(
        self,
        data=None,
        exp_id=None,
        exp_path=None,
        keydf_fname=None,
        cred_fname=None,
        devices=None,
        name=None,
        gsheet_key_path = None,
    ):
        self._data=Run_Table_DataFrame(
            data=data,
            exp_id=exp_id,
            exp_path=exp_path,
            keydf_fname=keydf_fname,
            cred_fname=cred_fname,
            devices=devices,
            name=name,
            )
        self.__dir__()

        self.gsheet_keys = AdjustableFS(gsheet_key_path, name="gsheet_keys", default_value='metadata thc gps xrd att att_usd kb')
    def append_run(self, runno, metadata,):
        self._data.append_run(runno, metadata)
        df = self._reduce_df()
        self._data.google_sheet.upload_all(df=df)
    def append_pos(self, name,):
        self._data.append_pos(name)
        df = self._reduce_df()
        self._data.google_sheet.upload_all(df=df)

    def _reduce_df(self, keys=None):
        if keys is None:
            keys = self.gsheet_keys()
        dfs = [self.__dict__[key].to_dataframe() for key in keys.split(' ') if key in self.__dir__()]
        dfc = self._concatenate_dfs(dfs)
        return dfc

    def _create_container(self):
        for n in np.unique(np.array([n.split('.')[0] for n in self._data.columns])):
            self.__dict__[n] = Container(df=self._data, name=n+'.')

    def _concatenate_dfs(self, dfs):
        dfc = dfs[0]
        for df in dfs[1:]:
            dfc = dfc.join(df)
        return dfc

    def __dir__(self):
        devs = np.unique(np.array([n.split('.')[0] for n in self._data.columns]))
        for dev in devs:
            if dev not in self.__dict__.keys():
                self.__dict__[dev] = Container(df=self._data, name=dev+'.')
        directory = self.__dict__.keys()
        return directory

    def __str__(self):
        devs = np.unique(np.array([n.split('.')[0] for n in self._data.columns]))
        devs_abc = np.array([dev[0] for dev in devs])
        devs_dict = {abc: devs[devs_abc == abc] for abc in np.unique(devs_abc)}
        devs_str = ''
        for key, value in devs_dict.items():
            devs_str = devs_str + f'{key.capitalize()}\n'
            for val in value:
                devs_str = devs_str + f'{val}\n'
            devs_str = devs_str + f'\n'
        return devs_str

    def __repr__(self):
        return self.__str__()

class Run_Table_DataFrame(DataFrame):
    def __init__(
        self,
        data=None,
        exp_id=None,
        exp_path=None,
        keydf_fname="/sf/bernina/config/src/python/gspread/gspread_keys.pkl",
        cred_fname="/sf/bernina/config/src/python/gspread/pandas_push",
        devices=None,
        name=None,
    ):
        super().__init__(data=data)

        ### Load devices to parse for adjustables ###
        devices = eco.__dict__[devices]
        self.devices = devices
        self.name = name
        self.fname = exp_path + f"{exp_id}_runtable.pkl"
        self.load()
        self.google_sheet = Gsheet_API(
            keydf_fname,
            cred_fname,
            exp_id,
            exp_path,
        )

        ### dicts holding adjustables and bad (not connected) adjustables ###
        self.adjustables = {}
        self.bad_adjustables = {}

        ###parsing options
        self._parse_exclude_keys = "status_indicators settings_collection status_indicators_collection presets memory _elog _currentChange _flags __ alias namespace daq scan evr _motor Alias".split(" ")
        self._parse_exclude_class_types = ("__ alias namespace daq scan evr _motor Alias AdjustablePv AxisPTZ".split(" "))
        self._adj_exclude_class_types = ("__ alias namespace daq scan evr _motor Alias".split(" "))
        self.key_order = "metadata xrd midir env_thc temperature1_rbk temperature2_rbk  time name gps gps_hex thc ocb eos las lxt phase_shifter mono att att_fe slit_und slit_switch slit_att slit_kb slit_cleanup pulse_id mono_energy_rbk att_transmission att_fe_transmission"
        pd.options.display.max_rows = 100
        pd.options.display.max_columns = 50
        pd.set_option("display.float_format", lambda x: "%.5g" % x)
    #def _get_values(self):
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
        pd.DataFrame(self).to_pickle(self.fname)

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
    ):
        dat = self._get_adjustable_values()
        dat["metadata"] = metadata
        dat["metadata"]["time"] = datetime.now()
        names = ["device", "adjustable"]
        multiindex = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names
        )
        values = np.array([val for adjs in dat.values() for val in adjs.values()])
        index = np.array([f'{dev}.{adj}' for dev, adjs in dat.items() for adj in adjs.keys()])
        #run_df = DataFrame([values], columns=multiindex, index=[runno])
        run_df = DataFrame([values], columns=index, index=[runno])
        self.df=self.append(run_df)
        self._remove_duplicates()
        #self.order_df()
        self.save()

    def append_pos(self, name=""):
        self.load()
        if len(self.adjustables) == 0:
            self._parse_parent_fewerparents()
        try:
            posno = int(self[self['metadata.type']=='pos'].index[-1].split("p")[1]) + 1
        except:
            posno = 0
        dat = self._get_adjustable_values()
        dat["metadata"] = {"time": datetime.now(), "name": name, "type": "pos"}
        names = ["device", "adjustable"]
        multiindex = pd.MultiIndex.from_tuples(
            [(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names
        )
        values = np.array([val for adjs in dat.values() for val in adjs.values()])
        index = np.array([f'{dev}.{adj}' for dev, adjs in dat.items() for adj in adjs.keys()])
        #pos_df = DataFrame([values], columns=multiindex, index=[f"p{posno}"])
        pos_df = DataFrame([values], columns=index, index=[f"p{posno}"])

        self.df=self.append(pos_df)
        self._remove_duplicates()
        #self.order_df()
        self.save()

    def _get_adjustable_values(self, silent=True):
        """
        This function gets the values of all adjustables in good adjustables and raises an error, when an adjustable is not connected anymore
        """
        if silent:
            dat = {}
            for devname, dev in self.good_adjustables.items():
                dat[devname] = {}
                bad_adjs = []
                for adjname, adj in dev.items():
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
                    self.bad_adjustables[devname][ba] = self.good_adjustables[devname].pop(ba)
        else:
            dat = {
                devname: {
                    adjname: adj.get_current_value() for adjname, adj in dev.items()
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
        self, device, adj_prefix=None, parent_name=None
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
                        self.adjustables[parent_name][".".join([name, key])] = value

        if parent_name == device.name:
            if hasattr(device, "get_current_value"):
                self.adjustables[parent_name]["self"] = device

    def _parse_child_instances_fewerparents(
        self, parent_class, adj_prefix=None, parent_name=None
    ):
        if parent_name is None:
            parent_name = parent_class.name
        self._get_all_adjustables_fewerparents(parent_class, adj_prefix, parent_name)
        if parent_name is not parent_class.name:
            if adj_prefix is not None:
                adj_prefix = ".".join([adj_prefix, parent_class.name])
            else:
                adj_prefix = parent_class.name

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
            [
                s
                for c in sub_classes
                for s in self._parse_child_instances_fewerparents(
                    c, adj_prefix, parent_name
                )
            ]
        )

    def _parse_parent_fewerparents(self, parent=None):
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
                        self.adjustables[s_class.name] = {}
                        self._parse_child_instances_fewerparents(s_class)
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

    def _check_adjustables(self, check_for_current_none_values=False):
        good_adj = {}
        bad_adj = {}
        for device, adjs in self.adjustables.items():
            good_dev_adj = {}
            bad_dev_adj = {}
            for name, adj in adjs.items():
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

