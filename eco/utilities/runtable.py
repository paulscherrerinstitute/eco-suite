import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pandas import DataFrame
import pandas as pd
import os
from pathlib import Path
from epics import PV
import numpy as np
import gspread_dataframe as gd
import gspread_formatting as gf
import gspread_formatting.dataframe as gf_dataframe
from datetime import datetime


class Run_Table():
    def __init__(self, pgroup, alias_namespace, devices, add_pvs={'pulse_id': 'SLAAR11-LTIM01-EVR0:RX-PULSEID'}):
        '''
        Additional PVs is a dictionary holding additional PVs to be saved to the dataframes
        '''
        self.alias_df = DataFrame()
        self.adj_df = DataFrame()
        self.alias_file_name = f'/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_alias_runtable.pkl'
        self.adj_file_name = f'/sf/bernina/data/{pgroup}/res/runtables/{pgroup}_adjustable_runtable.pkl'
        self._add_pvs = add_pvs
        self.devices = devices
        self._spreadsheet_key = '1H0nexCdIbYEOVH0wlQWSrR7E1lV6AL_j7JKd6DCkwLs'
        self._scope = ['https://spreadsheets.google.com/feeds', 
                       'https://www.googleapis.com/auth/drive']
        self._credentials = ServiceAccountCredentials.from_json_keyfile_name('/sf/bernina/config/src/python/gspread/pandas_push', self._scope)
        self.gc = gspread.authorize(self._credentials)
        self.keys = 'gps thc hex energy transmission hpos vpos hgap vgap shut delay lxt phase_shifter type name scan_motor pulse_id shut att_self att_fe_self las'
        self.key_order = 'metadata time type name scan_motor from to steps gps gps_hex thc las lxt phase_shifter xrd mono att att_fe slit_und slit_switch slit_att slit_kb slit_cleanup pulse_id mono_energy_rbk att_transmission att_fe_transmission'
        self.alias_namespace = alias_namespace
        self.adjustables = {}
        pd.options.display.max_rows = 999

        if os.path.exists(self.alias_file_name):
            #self.alias_df = pd.read_hdf(self.alias_file_name, key=None)
            self.alias_df = pd.read_pickle(self.alias_file_name)

        if os.path.exists(self.adj_file_name):
            #self.adj_df = pd.read_hdf(self.adj_file_name, key=None)
            self.adj_df = pd.read_pickle(self.adj_file_name)

    def _query_by_keys(self, keys='', df=None):
        if df is None:
            df = self.adj_df
        keys = keys.split(' ')
        if len(df.columns[0])>1:
            query_df = df[df.columns[np.array([np.any([ np.any([x in i for x in keys]) for i in col]) for col in df.columns])] ]
        else:
            query_df = df[df.columns[np.array([np.any([x in col for x in keys]) for col in df.columns])]]
        return query_df

    def query(self, keys = '', index = None, values = None, df=None):
        '''
        function to show saved data. keys is a string with keys separated by a space.
        All columns, which contain any of these strings are returned.
        Index can be a list od  indices.

        example: query(keys='xrd delay name', index = [0,5])
        will return all columns containing either xrd or delay and  show the data for runs 0 and 5

        example 2: query(keys = 'xrd delay name', index = ['p1', 'p2'])
        will return the same columns for the saved positions 1 and 2

        example 3: query(keys = 'xrd delay name', values='name == "(004)_peak"'])
        will return the same columns for the saved position, which was named (004)_peak

        example 4: query(keys = 'xrd delay name', values='xrd_xbase_readback > 10')
        will return all runs and positions, during which xrd.xbase was larger than 10.
        values is the pandas query
        '''
        query_df = self._query_by_keys(keys, df)
        if not values is None:
            query_df = query_df.query(values)
        query_df = query_df.T
        if not index is None:
            query_df = query_df[index]
        return query_df
    

    def _get_values(self):
        is_connected = np.array([pv.connected for pv in self._pvs.values()])
        filtered_dict = { key:pv.value for key, pv in self._pvs.items() if pv.connected }
        return filtered_dict

    def save(self):
        data_dir = Path(os.path.dirname(self.alias_file_name))

        if not data_dir.exists():
            print(
                f"Path {data_dir.absolute().as_posix()} does not exist, will try to create it..."
            )
            data_dir.mkdir(parents=True)
            print(f"Tried to create {data_dir.absolute().as_posix()}")
            data_dir.chmod(0o775)
            print(f"Tried to change permissions to 775")
        self.alias_df.to_pickle(self.alias_file_name)
        self.adj_df.to_pickle(self.adj_file_name)
        #self.alias_df.to_hdf(self.alias_file_name, key=None)
        #self.adj_df.to_hdf(self.adj_file_name, key=None)

    def append_run(self, runno, metadata={'type': 'ascan',
                                          'name': 'phi scan (001)',
                                          'scan_motor': 'phi', 
                                          'from': 1,
                                          'to': 2,
                                          'steps': 51}):
        dat = self._get_values()
        dat.update(metadata)
        dat['time'] = datetime.now()
        run_df = DataFrame([dat.values()], columns=dat.keys(), index=[runno])
        self.alias_df = self.alias_df.append(run_df)

        self._parse_parent()
        dat = self.adjustables
        dat['metadata']= metadata
        dat['metadata']['time'] = datetime.now()
        names = ['device','adjustable']
        multiindex = pd.MultiIndex.from_tuples([(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names)
        values = np.array([val for adjs in dat.values() for val in adjs.values()])
        run_df = DataFrame([values], columns=multiindex, index=[runno])
        self.adj_df = self.adj_df.append(run_df)
        self.save() 

    def append_pos(self, name=''):
        try: 
            posno = int(self.alias_df.query('type == "pos"').index[-1].split('p')[1])+1
        except:
            posno = 0
        dat = self._get_values()
        dat.update([('name', name), ('type', 'pos')])
        dat['time'] = datetime.now()
        pos_df = DataFrame([dat.values()], columns=dat.keys(), index=[f'p{posno}'])
        self.alias_df = self.alias_df.append(pos_df)

        self._parse_parent()
        dat = self.adjustables
        dat['metadata']= {'time':datetime.now(),  'name':name, 'type':'pos'}
        names = ['device','adjustable']
        multiindex = pd.MultiIndex.from_tuples([(dev, adj) for dev in dat.keys() for adj in dat[dev].keys()], names=names)
        values = np.array([val for adjs in dat.values() for val in adjs.values()])
        pos_df = DataFrame([values], columns=multiindex, index=[f'p{posno}'])
        self.adj_df = self.adj_df.append(pos_df)
        self.save() 

    def upload_rt(self, worksheet='runtable', keys=None, df=None):
        '''
        This function uploads all entries of which "type" contains "scan" to the worksheet positions.
        keys takes a string of keys separated by a space, e.g. 'gps xrd las'. All columns, which contain 
        any of these strings are uploaded. keys = None defaults to self.keys. keys = '' returns all columns
        '''
        self.order_df()
        if keys is None:
            keys = self.keys

        self.ws = self.gc.open_by_key(self._spreadsheet_key).worksheet(worksheet)
        if (len(keys) > 0):
            keys = keys+' type'
            upload_df = self._query_by_keys(keys=keys, df=df)
        else:
            upload_df = df
            if df is None:
                upload_df = self.adj_df
        upload_df = upload_df[upload_df['metadata']['type'].str.contains('scan', na=False)]
        gd.set_with_dataframe(self.ws, upload_df, include_index=True, col=2)
        gf_dataframe.format_with_dataframe(self.ws, upload_df, include_index=True, include_column_header=True, col=2)

    def upload_pos(self, worksheet='positions', keys=None):
        '''
        This function uploads all entries with "type == pos" to the worksheet positions.
        keys takes a list of strin All columns, which contain any of these strings are uploaded.
        keys = None defaults to self.keys. keys = [] returns all columns
        '''
        self.order_df()
        if keys is None:
            keys = self.keys

        self.ws = self.gc.open_by_key(self._spreadsheet_key).worksheet(worksheet)
        if (len(keys) > 0):
            keys = keys+' metadata'
            upload_df = self._query_by_keys(keys=keys)
        else:
            upload_df = self.alias_df
        upload_df = upload_df[upload_df['metadata']['type'].str.contains('pos', na=False)]
        gd.set_with_dataframe(self.ws, upload_df, include_index=True, col=2)
        gf_dataframe.format_with_dataframe(self.ws, upload_df, include_index=True, include_column_header=True, col=2)


    def _orderlist(self, mylist, key_order, orderlist=None):
        key_order = key_order.split(' ')
        if orderlist==None:
            index = np.concatenate([np.where(np.array(mylist)==k)[0] for k in key_order if k in mylist])
            #index = np.array([mylist.index(k) for k in key_order if k in mylist])
        else:
            index = np.concatenate([np.where(np.array(orderlist)==k)[0] for k in key_order if k in orderlist])
        curidx = np.arange(len(mylist))
        newidx = np.append(index, np.delete(curidx, index))
        return [mylist[n] for n in newidx]

    def order_df(self, key_order=None):
        '''
        This function orders the columns of the stored dataframe by the given key_order.
        key_order is a string with consecutive keys such as 'name type pulse_id. It defaults to self.key_order'
        '''
        if key_order is None:
            key_order = self.key_order
        self.alias_df = self.alias_df[self._orderlist(list(self.alias_df.columns), key_order)]
        devs = [ item[0] for item in list(self.adj_df.columns)]
        self.adj_df = self.adj_df[self._orderlist(list(self.adj_df.columns), key_order, orderlist=devs)]

    def get_all_adjustables(self, device, pp_name=None):
        if pp_name is not None:
            name = '_'.join([pp_name, device.name])
        else:
            name = device.name
        self.adjustables[name] = {key: value.get_current_value() for key, value in device.__dict__.items() if hasattr(value, 'get_current_value')}
        if hasattr(device, 'get_current_value'):
            self.adjustables[name]['_'.join([name, 'self'])]=device.get_current_value()

    def _parse_child_instances(self, parent_class, pp_name=None):
        try:
            self.get_all_adjustables(parent_class, pp_name)
        except:
            print(f'Getting adjustables from {parent_class.name} failed')
            pass
        if pp_name is not None:
            pp_name = '_'.join([pp_name, parent_class.name])
        else:
            pp_name = parent_class.name

        exclude = 'alias PV pv Record adjustable __ stage Delay'.split()
        sub_classes =  np.array( [s_class for s_class in parent_class.__dict__.values() if np.all([hasattr(s_class, '__dict__'), hasattr(s_class, 'name'), s_class.__hash__ is not None, 'eco' in str(type(s_class)), ~np.any([s in str(type(s_class)) for s in exclude])])])
        return set(sub_classes).union([ s for c in sub_classes for s in self._parse_child_instances(c, pp_name) ])

    def _parse_parent(self, parent=None):
        if parent == None:
            parent = self.devices
        exclude = '__ alias namespace config _mod evr daq scan'.split()
        for key in parent.__dict__.keys():
            if ~np.any([s in key for s in exclude]):
                if np.all([hasattr(parent.__dict__[key], '__dict__'), hasattr(parent.__dict__[key],'name')]):
                    #try:
                    self._parse_child_instances(parent.__dict__[key])
                    #except:
                    #    print(f'Getting adjustables from {key} failed')
                    #    pass

    def set_alias_namespace(self, alias_namespace):
        aliases = [s.replace('.', '_') for s in alias_namespace.aliases]
        self._alias_namespace = alias_namespace
        self._pvs = dict(zip(aliases, np.array([PV(ch, connection_timeout=0.05, auto_monitor=True) for ch in alias_namespace.channels])))
        self._pvs.update(dict(zip(self._add_pvs.keys(), np.array([PV(ch, connection_timeout=0.05, auto_monitor=True) for ch in self._add_pvs.values()]))))

    def get_alias_namespace(self):
        return self._alias_namespace
    alias_namespace = property(get_alias_namespace, set_alias_namespace)

    def __repr__(self):
        self.order_df()
        return_df =  self._query_by_keys(self.keys)
        return return_df.T.__repr__()
