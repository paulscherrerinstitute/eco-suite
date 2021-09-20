import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pandas import DataFrame
import pandas as pd
import warnings

warnings.simplefilter(action="ignore", category=pd.errors.PerformanceWarning)
import os
from pathlib import Path
from epics import PV
import numpy as np
import gspread_dataframe as gd
import gspread_formatting as gf
import gspread_formatting.dataframe as gf_dataframe
from datetime import datetime
import xlwt
import openpyxl
from ..devices_general.pv_adjustable import PvRecord
from epics import caget
import threading


class Run_Table:
    def __init__(
        self,
        pgroup=None,
        spreadsheet_key=None,
        devices=None,
        alias_namespace=None,
        channels_ca={"pulse_id": "SLAAR11-LTIM01-EVR0:RX-PULSEID"},
        name=None,
    ):

        ### Load device and alias_namespace after init of other devices ###
        if not devices:
            from eco import bernina

            devices = bernina
        self.devices = devices
        if not alias_namespace:
            from eco.aliases import NamespaceCollection

            alias_namespace = NamespaceCollection().bernina
        self.alias_namespace = alias_namespace

        self.name = name
        self.alias_df = DataFrame()
        self.adj_df = DataFrame()
        self.unit_df = DataFrame()
        self.alias_file_name = (
            f"/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_alias_runtable"
        )
        self.adj_file_name = (
            f"/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_adjustable_runtable"
        )
        self.unit_file_name = (
            f"/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_unit_runtable"
        )
        self.gspread_key_file_name = (
            f"/sf/bernina/config/src/python/gspread/gspread_keys"
        )

        self._channels_ca = channels_ca

        ### credentials and settings for uploading to gspread ###
        if not spreadsheet_key:
            spreadsheet_key = self._load_pgroup_gspread_keys(pgroup)
        self._spreadsheet_key = spreadsheet_key
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

        ### dicts holding adjustables and bad (not connected) adjustables ###
        self.adjustables = {}
        self.bad_adjustables = {}
        self.units = {}

        ###parsing options
        self._parse_exclude_keys =  "status_indicators settings_collection status_indicators_collection presets memory _elog _currentChange _flags __ alias namespace daq scan evr _motor Alias".split(' ')
        self._parse_exclude_class_types = "__ alias namespace daq scan evr _motor Alias AdjustablePv".split(' ')
        self._adj_exclude_class_types = "__ alias namespace daq scan evr _motor Alias".split(' ')

        pd.options.display.max_rows = 999
        pd.options.display.max_columns = 999
        pd.set_option('display.float_format', lambda x: '%.5g' % x)
        self.load()



    def _load_pgroup_gspread_keys(self, pgroup):
        if os.path.exists(self.gspread_key_file_name + ".pkl"):
            self.gspread_key_df = pd.read_pickle(self.gspread_key_file_name + ".pkl")
            if str(pgroup) in self.gspread_key_df.index:
                spreadsheet_key = self.gspread_key_df["keys"][f"{pgroup}"]
            else:
                spreadsheet_key = str(
                    input(
                        "Please enter the google spreadsheet key of pgroup {pgroup}, e.g. '1gK--KePLpYCs7U3QfNSPo69XipndbINe1Iz8to9bY1U': "
                    )
                )
                gspread_key_df = DataFrame(
                    {"keys": [spreadsheet_key]}, index=[f"{pgroup}"]
                )
                self.gspread_key_df = self.gspread_key_df.append(gspread_key_df)
                self.gspread_key_df.to_pickle(self.gspread_key_file_name + ".pkl")
        else:
            spreadsheet_key = str(
                input(
                    "Please enter the google spreadsheet key of pgroup {pgroup}, e.g. '1gK--KePLpYCs7U3QfNSPo69XipndbINe1Iz8to9bY1U': "
                )
            )
            self.gspread_key_df = DataFrame(
                {"keys": [spreadsheet_key]}, index=[f"{pgroup}"]
            )
            self.gspread_key_df.to_pickle(self.gspread_key_file_name + ".pkl")
        return spreadsheet_key

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
        All columns, which contain any of these strings are returned.
        Index can be a list od  indices.

        example: query(keys='xrd delay name', index = [0,5])
        will return all columns containing either xrd or delay and  show the data for runs 0 and 5

        example 2: query(keys = 'xrd delay name', index = ['p1', 'p2'])
        will return the same columns for the saved positions 1 and 2
        """
        self.load()
        #if len(keys) > 0:
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
        self.alias_df = self.alias_df[~self.alias_df.index.duplicated(keep="last")]
        self.unit_df = self.unit_df[~self.unit_df.index.duplicated(keep="last")]

    def save(self):
        data_dir = Path(os.path.dirname(self.alias_file_name + ".pkl"))
        if not data_dir.exists():
            print(
                f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
            )
            data_dir.mkdir(parents=True)
            print(f"Tried to create {data_dir.absolute().as_posix()}")
            data_dir.chmod(0o775)
            print(f"Tried to change permissions to 775")
        self.alias_df.to_pickle(self.alias_file_name + ".pkl")
        self.adj_df.to_pickle(self.adj_file_name + ".pkl")
        self.unit_df.to_pickle(self.unit_file_name + ".pkl")
        #self.alias_df.to_excel(self.alias_file_name + ".xlsx")
        #self.adj_df.to_excel(self.adj_file_name + ".xlsx")
        #self.unit_df.to_excel(self.unit_file_name + ".xlsx")

    def load(self):
        if os.path.exists(self.alias_file_name + ".pkl"):
            self.alias_df = pd.read_pickle(self.alias_file_name + ".pkl")
        if os.path.exists(self.adj_file_name + ".pkl"):
            self.adj_df = pd.read_pickle(self.adj_file_name + ".pkl")
        if os.path.exists(self.unit_file_name + ".pkl"):
            self.alias_df = pd.read_pickle(self.alias_file_name + ".pkl")

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
        dat = self._get_values()
        dat.update(metadata)
        dat["time"] = datetime.now()
        run_df = DataFrame([dat.values()], columns=dat.keys(), index=[runno])
        self.alias_df = self.alias_df.append(run_df)

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
                int(self.alias_df.query('type == "pos"').index[-1].split("p")[1]) + 1
            )
        except:
            posno = 0
        dat = self._get_values()
        dat.update([("name", name), ("type", "pos")])
        dat["time"] = datetime.now()
        pos_df = DataFrame([dat.values()], columns=dat.keys(), index=[f"p{posno}"])
        self.alias_df = self.alias_df.append(pos_df)

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
        self.alias_df = self.alias_df[
            self._orderlist(list(self.alias_df.columns), key_order)
        ]
        devs = [item[0] for item in list(self.adj_df.columns)]
        self.adj_df = self.adj_df[
            self._orderlist(list(self.adj_df.columns), key_order, orderlist=devs)
        ]

    def _get_adjustable_values(self):
        dat = {
            devname: {adjname: adj.get_current_value() for adjname, adj in dev.items()}
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
                        ~np.any([s in str(type(value)) for s in self._adj_exclude_class_types]),
                        hasattr(value, "get_current_value")
                    ]):
                    self.adjustables[name][key] = value

        if hasattr(device, "get_current_value"):
            self.adjustables[name][".".join([name, "self"])] = device

    def _get_all_adjustables_fewerparents(self, device, adj_prefix=None, parent_name=None):
        if adj_prefix is not None:
            name = ".".join([adj_prefix, device.name])
        else:
            name = device.name
        for key in device.__dict__.keys():
            if ~np.any([s in key for s in self._parse_exclude_keys]):
                value = device.__dict__[key]
                if np.all(
                    [
                        ~np.any([s in str(type(value)) for s in self._adj_exclude_class_types]),
                        hasattr(value, "get_current_value")
                    ]):
                    if parent_name == device.name:
                        self.adjustables[parent_name][key] = value
                    else:
                        self.adjustables[parent_name][".".join([name,key])] = value

        if parent_name == device.name:
            if hasattr(device, "get_current_value"):
                self.adjustables[parent_name]["self"] = device

    def _parse_child_instances_fewerparents(self, parent_class, adj_prefix=None, parent_name=None):
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
                        ~np.any([s in str(type(s_class)) for s in self._parse_exclude_class_types]),
                    ]
                ):
                   sub_classes.append(s_class)
        return set(sub_classes).union(
            [s for c in sub_classes for s in self._parse_child_instances_fewerparents(c, adj_prefix, parent_name)]
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
                            ~np.any([s in str(type(s_class)) for s in self._parse_exclude_class_types]),
                        ]
                    ):
                        self.adjustables[s_class.name] = {}
                        self._parse_child_instances_fewerparents(s_class)
            except Exception as e:
                print(e)
                print(key)
                #print(f"failed to parse {key} in runtable")
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
                        ~np.any([s in str(type(s_class)) for s in self._parse_exclude_class_types]),
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
                            ~np.any([s in str(type(s_class)) for s in self._parse_exclude_class_types]),
                        ]
                    ):
                        self._parse_child_instances(parent.__dict__[key])
            except Exception as e:
                print(e)
                print(key)
                #print(f"failed to parse {key} in runtable")
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
