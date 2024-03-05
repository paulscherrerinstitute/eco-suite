from pathlib import Path
from escape.swissfel import load_dataset_from_scan

from eco.elements.assembly import Assembly

class RunData(Assembly):
    def __init__(self,pgroup_adj,path_search='/sf/bernina/data/{pgroup:s}/raw',load_kwargs={},name=''):
        super().__init__(name=name)
        self._append(pgroup_adj,name='pgroup')
        self.path_search = path_search
        self.load_kwargs = load_kwargs
        self.loaded_runs={}

    def get_available_run_numbers(self):
        pgroup = self.pgroup.get_current_value()
        p = Path(self.path_search.format(pgroup=pgroup))
        runs = []
        for tp in p.iterdir():
            if not tp.is_dir():
                continue
            if tp.name[:3]=='run':
                numstring = tp.name.split('run')[1]
                if numstring.isdecimal():
                    runs.append(int(numstring))
        runs.sort()
        return runs
    
    def load_run(self,run_number,**kwargs):
        if run_number<0:
            run_number = self.get_available_run_numbers()[run_number]
            print(f'Loading run number {run_number}')
        tkwargs = self.load_kwargs
        tkwargs.update(kwargs)

        tks = {}
        for tk,tv in tkwargs.items():
            if type(tv) is str:
                tv = tv.format(pgroup=self.pgroup.get_current_value()) 
            tks[tk] = tv
        
        trun = load_dataset_from_scan(pgroup=self.pgroup.get_current_value(), run_numbers=[run_number],**tks)
        self.loaded_runs[run_number] = {'dataset':trun}
        return trun
    

    def get_run(self,run_number,**kwargs):
        if run_number<0:
            run_number = self.get_available_run_numbers()[run_number]
            print(f'Finding run number {run_number}')
        if run_number in self.loaded_runs.keys():
            return self.loaded_runs[run_number]['dataset']
        else:
            return self.load_run(run_number,**kwargs)
        
    def __getitem__(self,run_number):
        return self.get_run(run_number)
    

    

    

    
    
    




            
    

        

              