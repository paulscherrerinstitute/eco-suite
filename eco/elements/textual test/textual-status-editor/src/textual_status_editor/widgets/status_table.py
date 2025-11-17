from textual.app import App
from textual.widgets import Table, Input
from textual.reactive import Reactive
from textual.containers import Container
from eco.elements.protocols import Detector  # Assuming Detector is imported from the correct module
from .assembly_adapter import AssemblyAdapter  # Import the AssemblyAdapter

class StatusItem:
    def __init__(self, name, current_value):
        self.name = name
        self.current_value = current_value

    def set_target_value(self, value):
        # Logic to set the target value
        pass

class StatusTable(App):
    status_items: Reactive[list[StatusItem]] = Reactive([])

    def __init__(self, assembly_adapter: AssemblyAdapter):
        super().__init__()
        self.assembly_adapter = assembly_adapter

    async def on_mount(self):
        self.status_items = await self.assembly_adapter.get_status_items()
        self.render_table()

    def render_table(self):
        table = Table(title="Status Table")
        table.add_column("Name", justify="left")
        table.add_column("Current Value", justify="right")
        table.add_column("Set Target Value", justify="right")

        for item in self.status_items:
            input_field = Input(placeholder="Enter value", on_submit=self.set_value(item))
            table.add_row(item.name, str(item.current_value), input_field)

        self.set_widget(table)

    async def set_value(self, item: StatusItem, value: str):
        item.set_target_value(value)
        await self.assembly_adapter.update_status_item(item)

    def set_widget(self, widget):
        container = Container(widget)
        self.set_root(container)

if __name__ == "__main__":
    assembly_adapter = AssemblyAdapter()  # Initialize your adapter here
    StatusTable(assembly_adapter).run()