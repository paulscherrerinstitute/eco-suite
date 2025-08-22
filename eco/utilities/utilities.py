import json
from time import sleep, time
import sys, select
from threading import Thread

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any
import numpy as np
import matplotlib.pyplot as plt
from numbers import Number

import inspect

def is_notebook() -> bool:
    try:
        shell = get_ipython().__class__.__name__
        if shell == 'ZMQInteractiveShell':
            return True   # Jupyter notebook or qtconsole
        elif shell == 'TerminalInteractiveShell':
            return False  # Terminal running IPython
        else:
            return False  # Other type (?)
    except NameError:
        return False      # Probably standard Python interpreter

def foo_has_var_kw(foo):
    s = inspect.signature(foo)
    for param in s.parameters.values():
        if param.kind == inspect._ParameterKind.VAR_KEYWORD:
            return True
    return False

def foo_get_kwargs(foo):
    s = inspect.signature(foo)
    names = []
    for param in s.parameters.values():
        if param.kind == inspect._ParameterKind.POSITIONAL_OR_KEYWORD and not param.default==inspect._empty:
            names.append(param.name)
        elif param.kind == inspect._ParameterKind.KEYWORD_ONLY:
            names.append(param.name)
    return names


class NumpyEncoder(json.JSONEncoder):
    """Special json encoder for numpy types"""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


class TimeoutPath:
    executor = ThreadPoolExecutor(max_workers=1)

    def __init__(self, *args, timeout: float = 1, **kwargs):
        self._path = Path(*args, **kwargs)
        self.timeout = timeout

    def exists(self) -> bool:
        future = TimeoutPath.executor.submit(self._path.exists)
        try:
            return future.result(self.timeout)
        except TimeoutError:
            return False

    def get_path(self) -> Path:
        return self._path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._path, name)

    def __str__(self) -> str:
        return str(self._path)

class PropagatingThread(Thread):
    def run(self):
        run_start = time()
        self.exc = None
        try:
            if hasattr(self, "_Thread__target"):
                # Thread uses name mangling prior to Python 3.
                self.ret = self._Thread__target(
                    *self._Thread__args, **self._Thread__kwargs
                )
            else:
                self.ret = self._target(*self._args, **self._kwargs)
            
        except BaseException as e:
            self.exc = e

    def join(self):
        super(PropagatingThread, self).join()
        if self.exc:
            raise self.exc
        return self.ret

def isiter(a):
    try:
        iter(a)
        return True
    except TypeError:
        return False

def roundto(v,interval):
    return np.rint(v/interval)*interval

def linlog_intervals(*args, verbose=True, plot=False):
    """Get linearly and logarithmically spaced arrays from providing limits and intervals or number of intervals.
    Example usages:
    linlog_intervals(-1e-12,('lin',.1e-12),2e-12,('log',2e-12),1e-6,('lin',4),5e-6)

    Args:
        *args : limits and specifications of intervals, 
            limits are numbers, 
            specifications tuples of strings 'lin' or 'log' 
            and the definition if interval.
            A integer is interpretet as number of intervals within the limits,
            a float is interpreted asinterval size, 
            where in log definition, the size of the first (smallest) interval is matched.
        verbose (bool, optional): _description_. Defaults to True.
        plot (bool, optional): _description_. Defaults to False.

    Raises:
        Exception: _description_
        Exception: _description_
        Exception: _description_

    Returns:
        numpy ndarray: 1D array of intervals. 
    """
    limits = []
    idefs = []
    last_lim = False
    for arg in args:
        if not last_lim and isinstance(arg,Number):
            limits.append(arg)
            last_lim = True
        elif last_lim and isiter(arg):
            idefs.append(arg)
            last_lim = False
        else:
            raise Exception('Limits need to follow interval description and vice versa')
    if verbose:
        print(limits,idefs)
    if not len(limits)==len(idefs)+1:
        raise Exception('need exactly one more limit than interval definitions!')
            
    a = []
    for i,idef in enumerate(idefs):
        tlims = limits[i:i+2]
        if np.diff(tlims)<=0:
            raise Exception('number limits should increasing!')
        if isinstance(a,np.ndarray):
            if np.isclose(a[-1],tlims[0]):
                a = a[:-1]
            a = [a]
    
            
        if idef[0] == 'lin':
            if type(idef[1]) is int:
                a.append(np.linspace(*tlims,idef[1]+1))
            if type(idef[1]) is float:
                a.append(np.arange(tlims[0],tlims[1]+idef[1],idef[1]))
            if verbose:
                print(f'From {tlims[0]:5g} to {a[-1][-1]:5g}: {len(a[-1])-1} linear intervals of {np.mean(np.diff(a[-1])):5g} size')
            if plot:
                plt.plot(np.arange(len(np.hstack(a))-len(a[-1]), len(np.hstack(a))),a[-1],'db', mfc='none')
        if idef[0] == 'log':
            tlims = np.asarray(tlims)
            if type(idef[1]) is int:
                a.append(np.logspace(*np.log10(tlims),idef[1]+1))
            if type(idef[1]) is float:
                intlog = np.log10(tlims[0]+idef[1])-np.log10(tlims[0])
                a.append(10**np.arange(np.log10(tlims[0]),np.log10(tlims[1]),intlog))
            if verbose:
                intervals = np.diff(a[-1])
                print(f'From {tlims[0]:5g} to {a[-1][-1]:5g}: {len(a[-1])-1} logarithmic intervals between {np.min(intervals):5g} and {np.max(intervals):5g} in sizes.')
            if plot:
                plt.plot(np.arange(len(np.hstack(a))-len(a[-1]), len(np.hstack(a))),a[-1],'or',mfc='none')
        
        if verbose:
            if not np.isclose(a[-1][-1],tlims[1]):
                print(f'    NB: given limit {tlims[1]:5g} computes off by {tlims[1]-a[-1][-1]:5g} from last element!')
        
        
            
        a = np.hstack(a)
    if plot:
        plt.plot(np.arange(len(a)),a,'.k')
    if verbose:
        print(f'{len(a)} elements in total.') 
    return a
        
        
    

class ArrayTimestamp:
    def __init__(self, data=None, timestamps = None):
        if len(data) != len(timestamps):
            raise ValueError("Data and timestamps must have the same length.")
        if not isinstance(data, np.ndarray):
            data = np.array(data)
        if not isinstance(timestamps, np.ndarray):
            timestamps = np.array(timestamps)
        self.data = data
        self.timestamps = timestamps


class ScanTimestamp:
    def __init__(self, timestamp_intervals,par_steps):
        self.timestamp_intervals = timestamp_intervals
        
