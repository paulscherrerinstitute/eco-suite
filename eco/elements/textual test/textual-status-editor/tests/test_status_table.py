import pytest
from textual_status_editor.widgets.status_table import StatusTable
from textual_status_editor.models.status_item import StatusItem

@pytest.fixture
def status_items():
    return [
        StatusItem(name="Item 1", current_value=10),
        StatusItem(name="Item 2", current_value=20),
        StatusItem(name="Item 3", current_value=30),
    ]

def test_status_table_display(status_items):
    table = StatusTable(status_items)
    rendered = table.render()

    assert "Item 1" in rendered
    assert "10" in rendered
    assert "Item 2" in rendered
    assert "20" in rendered
    assert "Item 3" in rendered
    assert "30" in rendered

def test_status_table_set_target_value(status_items):
    table = StatusTable(status_items)
    table.set_target_value("Item 1", 15)

    assert status_items[0].current_value == 15

def test_status_table_invalid_target_value(status_items):
    table = StatusTable(status_items)
    table.set_target_value("Item 4", 25)  # Non-existent item

    assert status_items[0].current_value == 10  # Should remain unchanged
    assert status_items[1].current_value == 20
    assert status_items[2].current_value == 30