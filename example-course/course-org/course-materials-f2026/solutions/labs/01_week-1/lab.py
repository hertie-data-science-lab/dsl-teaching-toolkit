"""Worked solution to lab 1 - released the evening AFTER the lab (see schedule.yml).

Staged here rather than inside `labs/01_week-1/`, which is released at lab time: a
solutions folder in there would ship with it.
"""


def perceptron_step(w, x, y, lr=0.1):
    return w + lr * (y - (1 if w @ x > 0 else 0)) * x
