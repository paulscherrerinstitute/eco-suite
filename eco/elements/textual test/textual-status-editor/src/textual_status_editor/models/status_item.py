from textual import events
from textual.widget import Widget
from textual.reactive import Reactive
from textual.containers import Container
from textual.widgets import Input, Table


class StatusItem:
    def __init__(self, name, current_value):
        self.name = name
        self.current_value = current_value

    def set_target_value(self, value):
        # Logic to set the target value
        self.current_value = value


class StatusItemWidget(Widget):
    name: str
    current_value: Reactive[str] = Reactive("")

    def __init__(self, status_item: StatusItem):
        super().__init__()
        self.status_item = status_item
        self.name = status_item.name
        self.current_value = str(status_item.current_value)

    def render(self):
        return f"{self.name}: {self.current_value}"

    async def on_input_changed(self, event: events.InputChanged):
        if event.input.value:
            self.status_item.set_target_value(event.input.value)
            self.current_value = event.input.value
            await self.refresh()


class StatusItemTable(Widget):
    def __init__(self, status_items):
        super().__init__()
        self.status_items = status_items
        self.table = Table()

    def render(self):
        self.table.clear()
        self.table.add_column("Item Name")
        self.table.add_column("Current Value")
        self.table.add_column("Set Value")

        for item in self.status_items:
            row = [item.name, str(item.current_value), Input(placeholder="Set value")]
            self.table.add_row(*row)

        return Container(self.table)

    async def on_input_changed(self, event: events.InputChanged):
        for item in self.status_items:
            if event.input.value:
                item.set_target_value(event.input.value)
                await self.refresh()