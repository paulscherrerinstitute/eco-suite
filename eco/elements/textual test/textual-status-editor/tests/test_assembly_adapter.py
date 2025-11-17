import pytest
from textual_status_editor.adapters.assembly_adapter import AssemblyAdapter
from textual_status_editor.models.status_item import StatusItem

@pytest.fixture
def assembly_adapter():
    return AssemblyAdapter()

def test_get_current_values(assembly_adapter):
    # Mock the status collection to return predefined values
    assembly_adapter.status_collection = [
        StatusItem(name="Item1", current_value=10),
        StatusItem(name="Item2", current_value=20),
    ]
    
    current_values = assembly_adapter.get_current_values()
    
    assert current_values == {
        "Item1": 10,
        "Item2": 20,
    }

def test_set_target_value(assembly_adapter):
    # Mock the status item
    item = StatusItem(name="Item1", current_value=10)
    assembly_adapter.status_collection = [item]
    
    assembly_adapter.set_target_value("Item1", 15)
    
    assert item.target_value == 15

def test_set_target_value_nonexistent_item(assembly_adapter):
    # Mock the status collection
    assembly_adapter.status_collection = [
        StatusItem(name="Item1", current_value=10),
    ]
    
    result = assembly_adapter.set_target_value("NonexistentItem", 15)
    
    assert result is False  # Expecting failure when item does not exist

def test_update_status_item(assembly_adapter):
    item = StatusItem(name="Item1", current_value=10)
    assembly_adapter.status_collection = [item]
    
    assembly_adapter.update_status_item("Item1", 20)
    
    assert item.current_value == 20

def test_update_status_item_nonexistent(assembly_adapter):
    item = StatusItem(name="Item1", current_value=10)
    assembly_adapter.status_collection = [item]
    
    result = assembly_adapter.update_status_item("NonexistentItem", 20)
    
    assert result is False  # Expecting failure when item does not exist