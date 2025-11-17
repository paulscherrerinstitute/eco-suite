from textual.app import App
from textual.widgets import Static, Input, Table
from textual.reactive import Reactive
from textual import events

from .adapters.assembly_adapter import AssemblyAdapter

class StatusItem:
    def __init__(self, name, current_value):
        self.name = name
        self.current_value = current_value

    def set_target_value(self, value):
        # Logic to set the target value
        self.current_value = value

class StatusTable(Table):
    def __init__(self, items):
        super().__init__()
        self.items = items
        self.add_column("Name", min_width=20)
        self.add_column("Current Value", min_width=20)
        self.add_column("Set Target Value", min_width=20)

        for item in self.items:
            self.add_row(item.name, str(item.current_value), "")

    async def on_input_changed(self, event: events.InputChanged):
        row_index = event.row_index
        column_index = event.column_index
        if column_index == 2:  # Assuming the third column is for setting target values
            target_value = event.value
            self.items[row_index].set_target_value(target_value)
            self.update_row(row_index)

    def update_row(self, row_index):
        item = self.items[row_index]
        self.update_row(row_index, item.name, str(item.current_value), "")

class StatusEditorApp(App):
    def __init__(self):
        super().__init__()
        self.adapter = AssemblyAdapter()
        self.status_items = self.adapter.get_status_items()
        self.table = StatusTable(self.status_items)

    async def on_mount(self):
        await self.view.dock(self.table)

if __name__ == "__main__":
    app = StatusEditorApp()
    app.run()