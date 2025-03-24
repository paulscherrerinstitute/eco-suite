
import gspread
import numpy as np


class RuntableGsheet:
    def __init__(self,sheet,
                 wstitle_available_keys='Available keys',
                 range_available_keys = ['A2','A10000000'],
                 wstitle_run_table='Custom table',
                 range_run_table_keys=['A1','ZZZ1'],
                 name_delimiter='/',
                 remove_leading=''):
        self._spreadsheet = sheet
        self._wstitle_available_keys = wstitle_available_keys
        self._range_available_keys = range_available_keys
        self._wstitle_run_table = wstitle_run_table
        self._range_run_table_keys = range_run_table_keys
        self._name_delimiter = name_delimiter
        self._remove_leading = remove_leading

    def require_worksheets(self):
        tls = [tmp.title for tmp in self._spreadsheet.worksheets()]
        for title in [self._wstitle_available_keys, self._wstitle_run_table]:
            if not title in tls:
                self._spreadsheet.add_worksheet(title,1,1)        
    
    def get_available_keys(self):
        ks = self._spreadsheet.worksheet(self._wstitle_available_keys).get_values(':'.join(self._range_available_keys))
        return [x for xs in ks for x in xs]

    def set_available_keys(self,keys):
        rng = gspread.utils.a1_range_to_grid_range(':'.join(self._range_available_keys))
        shape = (rng['endColumnIndex']-rng['startColumnIndex'], rng['endRowIndex']-rng['startRowIndex'])
        cells = []
        nrowstot = 0
        ncolstot = 0
        for i,k in enumerate(keys):
            ti = np.unravel_index(i, shape, order='C')
            cells.append(
                gspread.Cell(
                    ti[1]+rng['startRowIndex']+1,
                    ti[0]+rng['startColumnIndex']+1,
                    k.split(self._remove_leading)[1]))
            nrowstot = int(max(nrowstot,ti[1]+rng['startRowIndex']+1))
            ncolstot = int(max(ncolstot,ti[0]+rng['startColumnIndex']+1))
        
        if cells:
            ws = self._spreadsheet.worksheet(self._wstitle_available_keys)
            nr = ws.row_count
            if nr < nrowstot:
                ws.add_rows(nrowstot-nr)
            nc = ws.col_count
            if nc < ncolstot:
                ws.add_cols(ncolstot-nc)

            ws.update_cells(cells)

    def fill_run_table_data(self, table):
        cell_list = self._spreadsheet.worksheet(self._wstitle_run_table).range(':'.join(self._range_run_table_keys))
        set_cells= []
        nrowstot = 0
        ncolstot = 0
        test_keys = [tk.split(self._remove_leading)[1] for tk in table.keys()]
        for cell in cell_list:
            tstr = cell.value
            if not isinstance(tstr,str):
                continue
            tstr = tstr.split(self._name_delimiter)[0].strip()

            if tstr in test_keys:
                set_vals = table[tstr]
                for n,set_val in enumerate(set_vals):
                    set_cells.append(gspread.Cell(cell.row + n + 1, cell.col, set_val))
                    nrowstot = int(max(nrowstot,cell.row + n + 1))
                    ncolstot = int(max(ncolstot,cell.col))
        if set_cells:
            ws = self._spreadsheet.worksheet(self._wstitle_run_table)
            nr = ws.row_count
            if nr < nrowstot:
                ws.add_rows(nrowstot-nr)
            nc = ws.col_count
            if nc < ncolstot:
                ws.add_cols(ncolstot-nc)
            ws.update_cells(set_cells)



