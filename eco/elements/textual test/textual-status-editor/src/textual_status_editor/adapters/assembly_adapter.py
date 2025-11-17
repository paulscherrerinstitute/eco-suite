from eco.elements.protocols import Assembly
from textual.app import App
from textual.widgets import Table, Input
from textual.reactive import Reactive
from textual.containers import Container

class StatusItem:
    def __init__(self, name, current_value):
        self.name = name
        self.current_value = current_value

    def set_target_value(self, value):
        # Placeholder for setting the target value
        self.current_value = value

class AssemblyAdapter:
    def __init__(self, assembly: Assembly):
        self.assembly = assembly

    def get_status_items(self):
        status_items = []
        for item in self.assembly.status_collection.get_list():
            current_value = item.get_current_value() if hasattr(item, 'get_current_value') else None
            status_items.append(StatusItem(item.alias.get_full_name(), current_value))
        return status_items

class StatusTable(App):
    def __init__(self, assembly_adapter: AssemblyAdapter):
        super().__init__()
        self.assembly_adapter = assembly_adapter
        self.status_items = self.assembly_adapter.get_status_items()

    async def on_mount(self):
        self.table = Table()
        self.table.add_column("Name")
        self.table.add_column("Current Value")
        self.table.add_column("Set Target Value")

        for item in self.status_items:
            input_field = Input(placeholder="Enter value")
            input_field.on_submit(lambda value, item=item: self.set_target_value(item, value))
            self.table.add_row(item.name, str(item.current_value), input_field)

        await self.view.dock(self.table)

    def set_target_value(self, item: StatusItem, value: str):
        item.set_target_value(value)
        self.refresh_table()

    def refresh_table(self):
        self.table.clear()
        for item in self.status_items:
            input_field = Input(placeholder="Enter value")
            input_field.on_submit(lambda value, item=item: self.set_target_value(item, value))
            self.table.add_row(item.name, str(item.current_value), input_field)

if __name__ == "__main__":
    assembly = Assembly()  # Replace with actual assembly initialization
    adapter = AssemblyAdapter(assembly)
    app = StatusTable(adapter)
    app.run()