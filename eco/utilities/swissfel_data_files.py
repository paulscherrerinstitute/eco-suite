from pathlib import Path

class DataFiles:
    def __init__(self,pgroup=None, data_path='/sf/bernina/data/'):
        self.pgroup = pgroup
        self.data_path = data_path
        self.raw = Path(self.data_path) / Path(pgroup) / Path('raw')
    
    def get_available_runs(parse_numbers=True):
        self.raw

    