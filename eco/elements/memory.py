from pathlib import Path
from datetime import datetime 
from ..devices_general.adjustable import AdjustableFS
from tabulate import tabulate

global_memory_dir = None

def set_global_memory_dir(dirpath,mode='w'):
    globals()['global_memory_dir'] = Path(dirpath).expanduser()

def get_memory(name):
    if not (global_memory_dir is None):
        return Memory(name)

class Memory:
    def __init__(self,obj,memory_dir=global_memory_dir,categories={'recall':['settings'],'track':['status_indicators']}):
        self.obj_parent = obj
        self.categories = categories
        if not memory_dir:
            memory_dir = global_memory_dir
        self.base_dir = Path(memory_dir)

    def setup_path(self):    
        name = self.obj_parent.alias.get_full_name(joiner=None)
        self.dir = Path(self.base_dir) / Path('/'.join(reversed(name)))
        self.memories = AdjustableFS(self.dir/Path('memories.json'),default_value={})
        try:
            self.dir.mkdir(exist_ok=True)
        except:
            print('Could not create memory directory')
    
    def __str__(self):
        self.setup_path()
        mem = self.memories()
        a = []
        for n,(key,content) in enumerate(mem.items()):
            row = [n]
            t = datetime.fromisoformat(key)
            row.append(t.strftime('%Y-%m-%d: %a %-H:%M'))
            row.append(content['message'])
            a.append(row)
        return (tabulate(a,headers=["Index","Time","Message"]))

        
    def memorize(self, message=None, attributes={}, force_message=True):
        self.setup_path()
        stat_now = self.obj_parent.get_status()
        stat_now['memorized_attributes'] = attributes
        key = datetime.now().isoformat()
        mem = self.memories()
        if force_message:
            while not message:
                message = input("Please enter a message associated to this memory entry:\n>>> ")
        mem[key] = {'message':message,'categories':self.categories}
        tmp = AdjustableFS(self.dir / Path(key + '.json'))
        tmp(stat_now)
        self.memories(mem)

    def get_memory(self,index=None,key=None):
        self.setup_path()
        if not (index is None):
            key = list(self.memories().keys())[index]
        tmp = AdjustableFS(self.dir / Path(key + '.json'))
        return tmp()
         
    def recall(self,memory_index=None,key=None):
        # mem = self.get_memory(index=memory_index,key=key)
        # rec = mem['settings']
        # for n,(key,value) in enumerate(rec.items()):
        #     row = [n]
        #     present_value = key.split('.')

        # stat_now = self.obj_parent.get_status()
        # for mem
        pass

    def __repr__(self):
        return self.__str__()


def name2obj(obj_parent,name, delimiter='.'):
    if type(name) is str:
        name = name.split(delimiter)
    obj = obj_parent
    for tn in name:
        obj = obj.__dict__[tn]
    return name