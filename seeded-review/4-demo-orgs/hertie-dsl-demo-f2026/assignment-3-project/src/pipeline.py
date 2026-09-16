# source: https://github.com/hertie-dsl-demo-f2026/assignment-3-project/blob/main/src/pipeline.py @ 77aa361
"""Project pipeline - the single entry point for your whole analysis.

Keep `run_pipeline()` as the entry point: load, split, fit, evaluate, report. The structural
checks look for this function by name, and a marker has to be able to reproduce your numbers
with one command.

Replace everything below the imports. Delete this docstring's instructions when you do.
"""

from __future__ import annotations

SEED = 2026  # fix it once, here, and state it in REPORT.md


def load_data():
    """Read the raw data and return it. Do not transform anything here.

    Fetch from the source (or read a file your data/README.md explains how to obtain) and
    return a data frame. Keep raw data out of the repository.
    """
    raise NotImplementedError


def split(data):
    """Split into train and test ONCE, before any exploration of the target.

    Choose the scheme that matches your dependence structure: random, grouped (repeated
    units), or time-based (never train on the future). Say which in REPORT.md section 3.
    """
    raise NotImplementedError


def build_model():
    """Return an unfitted estimator with every preprocessing step inside it.

    Use a Pipeline / ColumnTransformer so that cross-validation refits the preprocessing
    inside each fold. This is what makes the leakage in session 9 impossible by accident.
    """
    raise NotImplementedError


def evaluate(model, X_test, y_test):
    """Score the fitted model and return a dict of metrics.

    Include the baseline, an interval (bootstrap or repeated CV), and per-subgroup numbers
    where a subgroup matters.
    """
    raise NotImplementedError


def run_pipeline():
    """Load, split, fit, evaluate - and return the metrics dict you report.

    Must be runnable end to end from a clean checkout:  python -m src.pipeline
    """
    raise NotImplementedError


if __name__ == "__main__":
    print(run_pipeline())
