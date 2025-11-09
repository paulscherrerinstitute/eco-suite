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
        pass

class StatusTable(Table):
    def __init__(self, items):
        super().__init__()
        self.items = items
        self.add_column("Name", min_width=20)
        self.add_column("Current Value", min_width=20)
        self.add_column("Set Target Value", min_width=20)

        for item in self.items:
            self.add_row(item.name, str(item.current_value), Input(placeholder="Set value"))

    async def on_input_changed(self, event: events.InputChanged):
        # Logic to handle input changes
        row_index = self.get_row_index(event.sender)
        if row_index is not None:
            item = self.items[row_index]
            item.set_target_value(event.sender.value)

class StatusEditorApp(App):
    def __init__(self):
        super().__init__()
        self.adapter = AssemblyAdapter()
        self.status_items = self.adapter.get_status_items()

    async def on_mount(self):
        self.table = StatusTable(self.status_items)
        await self.view.dock(self.table)

if __name__ == "__main__":
    StatusEditorApp.run()