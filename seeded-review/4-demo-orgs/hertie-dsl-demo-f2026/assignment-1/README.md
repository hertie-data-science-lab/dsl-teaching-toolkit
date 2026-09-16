<!-- source: https://github.com/hertie-dsl-demo-f2026/assignment-1/blob/main/README.md @ a89c859 -->
# Assignment 1 - linear regression from scratch (individual)

**Weight:** 15% of the final mark
**Due:** Tuesday 13 October 2026, 23:59 (Europe/Berlin)
**Submission:** push to `main` in this repository - that push *is* your submission.

## The task

Implement ordinary least squares yourself, in `starter.py`, using **only the Python standard
library**. No NumPy, no pandas, no scikit-learn. The point is that you can derive and code
the estimator once before delegating it for the rest of your career.

Fill in the four functions:

| Function | Returns |
|---|---|
| `fit_linear_regression(X, y)` | coefficients `[b0, b1, ..., bp]`, intercept **first** |
| `predict(X, beta)` | a list of fitted values, one per row of `X` |
| `r_squared(y_true, y_pred)` | the coefficient of determination |
| `solve_linear_system(A, b)` | solves `A z = b`; raises `ValueError` if `A` is singular |

`X` is a list of rows, each row a list of feature values, **without** a column of ones -
`fit_linear_regression` adds the intercept itself.

## What you are implementing

Minimise the residual sum of squares. Setting the gradient to zero gives the normal
equations:

```
X'X b = X'y
```

Do **not** compute `(X'X)^-1`. Build `X'X` and `X'y`, then solve the system with Gaussian
elimination with partial pivoting - that is what `solve_linear_system` is for, and it is the
part of the assignment where the marks are.

Definition of R-squared, for the avoidance of doubt:

```
R^2 = 1 - RSS/TSS,   RSS = sum (y_i - y_hat_i)^2,   TSS = sum (y_i - mean(y))^2
```

## What is assessed

- **10 marks, automated.** Hidden tests, run after the deadline. They check exact recovery on
  noiseless data, the two defining properties of a least-squares fit (residuals sum to zero
  and are orthogonal to every feature), that no perturbation of your coefficients lowers the
  RSS, and the three boundary values of R-squared (1, 0, and negative).
- **5 marks, by hand.** Code clarity, docstrings, and the two short answers below.

## Short answers

Add these to the bottom of `starter.py` as a module-level docstring or comment block, 3-5
sentences each:

1. Why does partial pivoting matter numerically, and what goes wrong without it?
2. Your `fit_linear_regression` fails on a design matrix containing both `area_in_sqm` and
   `area_in_sqft`. Explain why in terms of `X'X`, and say what you would do about it.

## Rules

- Standard library only. `import math` is fine; `import numpy` will fail the tests.
- Do not rename the functions, change their signatures, or move them out of `starter.py`.
- Keep the file importable: no code that runs on import beyond definitions (guard any demo
  with `if __name__ == "__main__":`).
- Individual work. Name anyone you discussed it with in a comment; that costs you nothing.
- Declare any AI assistance, and be able to explain every line you submit.

## Checking your own work

You do not get the hidden tests, but you can check the properties they check:

```python
X = [[32], [45], [52], [60], [68], [75], [80], [95]]
y = [540, 510, 640, 545, 720, 620, 770, 860]
beta = fit_linear_regression(X, y)
resid = [yi - yh for yi, yh in zip(y, predict(X, beta))]
print(sum(resid))                                   # ~0
print(sum(r * row[0] for r, row in zip(resid, X)))  # ~0
```

Both numbers must be zero to within floating-point tolerance. If they are not, your solve is
wrong, and every other test will fail too.
