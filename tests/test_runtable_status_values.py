"""The run table's `d=` argument: which shape it accepts, and why.

`Run_Table_DataFrame._get_adjustable_values` looks names up as
``"bernina." + name`` in a flat mapping. `Daq` passed it the whole
`get_status()` result instead, so nothing ever matched and it silently fell
back to reading every adjustable over Channel Access - a full CA fan-out at
every scan start, while apparently being handed the values.
"""

import pytest

runtable = pytest.importorskip("eco.utilities.runtable")


def test_flat_mapping_is_passed_through():
    d = {"bernina.att.transmission": 0.5}
    assert runtable.Run_Table_DataFrame._status_values(d) == d


def test_get_status_shape_is_unwrapped():
    """The shape Daq was passing."""
    d = {
        "status": {"bernina.att.transmission": 0.5},
        "status_channels": {"bernina.att.transmission": "PV:X"},
        "status_times": {},
        "selections": {},
    }
    assert runtable.Run_Table_DataFrame._status_values(d) == {
        "bernina.att.transmission": 0.5
    }


def test_an_older_settings_block_is_merged_in():
    d = {"status": {"a": 1}, "settings": {"b": 2}}
    assert runtable.Run_Table_DataFrame._status_values(d) == {"a": 1, "b": 2}


def test_empty_and_none_are_empty():
    assert runtable.Run_Table_DataFrame._status_values({}) == {}
    assert runtable.Run_Table_DataFrame._status_values(None) == {}


def test_a_status_key_that_is_not_a_mapping_is_left_alone():
    """A channel literally called "status" must not be mistaken for the
    wrapper."""
    d = {"status": 3, "bernina.x": 1}
    assert runtable.Run_Table_DataFrame._status_values(d) == d
