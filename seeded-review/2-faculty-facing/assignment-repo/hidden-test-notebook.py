# HIDDEN tests - run faculty-side by the Grade assignment workflow, never shipped to students.
# The submitted notebook is nbconvert'd to starter.py first; this imports it and checks it.
# Replace this placeholder with the real grading tests.
from starter import solve


def test_solve_runs():
    assert solve() is not None
