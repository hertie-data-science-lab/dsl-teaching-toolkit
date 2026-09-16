# source: https://github.com/hertie-dsl-demo-f2026/assignment-1/blob/main/starter.py @ a89c859
"""Assignment 1 - linear regression from scratch.

Standard library only: no numpy, no pandas, no scikit-learn.

Fill in the four functions below. Keep the names and signatures exactly as they are - the
grading tests import them by name. See README.md for what is assessed.

Your two short answers go at the bottom of this file.
"""


def solve_linear_system(A, b):
    """Solve `A z = b` by Gaussian elimination with partial pivoting.

    Args:
        A: an n x n matrix, as a list of n rows (each a list of n floats).
        b: the right-hand side, a list of n floats.

    Returns:
        The solution vector `z`, a list of n floats.

    Raises:
        ValueError: if `A` is singular (no unique solution exists).

    Hint: work on a copy of the augmented matrix. For each column, find the row with the
    largest absolute value in that column (the pivot), swap it up, then eliminate the
    entries below it. Finish with back-substitution.
    """
    raise NotImplementedError("implement solve_linear_system")


def fit_linear_regression(X, y):
    """Fit ordinary least squares, returning coefficients with the intercept FIRST.

    Args:
        X: a list of n rows, each a list of p feature values. No column of ones - this
           function adds the intercept itself.
        y: a list of n outcome values.

    Returns:
        A list of p + 1 coefficients: [intercept, b_1, ..., b_p].

    Build the normal equations X'X b = X'y (with the intercept column prepended to X) and
    hand them to `solve_linear_system`. Do NOT invert X'X.
    """
    raise NotImplementedError("implement fit_linear_regression")


def predict(X, beta):
    """Fitted values for each row of `X`.

    Args:
        X: a list of n rows, each a list of p feature values (again, no column of ones).
        beta: a list of p + 1 coefficients as returned by `fit_linear_regression`.

    Returns:
        A list of n predictions.
    """
    raise NotImplementedError("implement predict")


def r_squared(y_true, y_pred):
    """Coefficient of determination, 1 - RSS/TSS.

    Args:
        y_true: the observed outcomes.
        y_pred: the predicted outcomes, same length.

    Returns:
        A float. 1.0 for a perfect fit, 0.0 for predicting the mean of `y_true`, and
        negative for predictions worse than that mean.
    """
    raise NotImplementedError("implement r_squared")


if __name__ == "__main__":
    # A place to try things out. Nothing here is graded, and nothing here runs on import.
    X = [[32], [45], [52], [60], [68], [75], [80], [95]]
    y = [540, 510, 640, 545, 720, 620, 770, 860]
    beta = fit_linear_regression(X, y)
    print("coefficients:", beta)
    print("R-squared:", r_squared(y, predict(X, beta)))


# ----------------------------------------------------------------------------------
# SHORT ANSWERS (3-5 sentences each - see README.md)
#
# 1. Why does partial pivoting matter numerically, and what goes wrong without it?
#
#    <your answer>
#
# 2. Your fit fails on a design matrix containing both `area_in_sqm` and `area_in_sqft`.
#    Explain why in terms of X'X, and say what you would do about it.
#
#    <your answer>
# ----------------------------------------------------------------------------------
