# HIDDEN tests - the notebook is nbconvert'd to starter.py, then this imports it.
from starter import solve


def test_endpoints():
    out = solve([0, 5, 10])
    assert out[0] == 0 and out[-1] == 1


def test_midpoint():
    assert abs(solve([0, 5, 10])[1] - 0.5) < 1e-9
