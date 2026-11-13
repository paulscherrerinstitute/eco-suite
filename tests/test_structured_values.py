"""eco.widgets.structured_values: dict/list values for the editors -- JSON
form with read-only leaves, and the types restored on the way back."""

import numpy as np
import pytest

from eco.widgets import structured_values as sv


def test_to_jsonable_marks_what_is_not_plain():
    value = {"a": 1, "b": [np.float64(2.5), np.arange(2)], 3: "x", "nan": float("nan")}
    data, read_only = sv.to_jsonable(value)
    assert data["a"] == 1 and data["b"][0] == 2.5 and isinstance(data["b"][0], float)
    assert data["b"][1] == repr(np.arange(2))
    assert sorted(map(tuple, read_only)) == [("3",), ("b", 1), ("nan",)]


def test_coerce_like_restores_types():
    old = {"f": 1.5, "i": 2, "t": (1, 2), "np": np.float32(1), "arr": np.arange(2), "s": "x", 3: "y"}
    new = {"f": 2, "i": 3.0, "t": [4, 5], "np": 2, "arr": repr(np.arange(2)), "s": 5, "3": "z", "added": 7}
    out = sv.coerce_like(new, old)
    assert out["f"] == 2.0 and isinstance(out["f"], float)
    assert out["i"] == 3 and isinstance(out["i"], int)
    assert out["t"] == (4, 5)
    assert isinstance(out["np"], np.float32)
    assert out["arr"] is old["arr"]
    assert out["s"] == "5"
    assert out[3] == "z" and "3" not in out  # the int key comes back as int
    assert out["added"] == 7


def test_parse_leaf():
    assert sv.parse_leaf("2", kind="float") == 2.0
    assert sv.parse_leaf("off", kind="bool") is False
    with pytest.raises(ValueError):
        sv.parse_leaf("2.5", kind="int")
    with pytest.raises(ValueError):
        sv.parse_leaf("fast", kind="float")
    assert sv.parse_leaf("[1, 2]") == [1, 2]
    assert sv.parse_leaf("true") is True  # JSON
    assert sv.parse_leaf("plain words") == "plain words"
