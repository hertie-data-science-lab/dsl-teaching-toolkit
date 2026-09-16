<!-- source: https://github.com/hertie-dsl-demo-course-e1234/assignment-2-f2026/blob/main/README.md @ a730e02 -->
# Assignment 2 - classification and evaluation (individual)

Implement logistic regression and its evaluation yourself in `starter.ipynb`, standard
library only - `sigmoid`, `log_loss`, `predict_proba`, `fit_logistic`, `confusion_counts`,
`precision_recall_f1` and `choose_threshold` - then use them to choose and defend a
decision threshold for a lending decision.

Push to `main` in your assignment repository - that push is your submission.
Due: Tuesday 29 September 2026, 23:59 (Europe/Berlin). Worth 20% of the final mark.

## What is assessed

- **8 marks, automated.** Hidden tests run after the deadline against the seven functions.
- **7 marks, by hand.** Code quality: clear names, no repetition, the numerical guards the
  docstrings ask for.
- **5 marks, by hand.** Your write-up in section 9 - the cost ratio, the threshold it
  implies, and one thing this evaluation cannot settle.

## Before you start

The grader converts this notebook to a script and imports it, so **every top-level cell
runs at grading time**. A cell that raises stops the import and costs you all eight
automated marks. Keep the exploratory cells inside their `try/except NotImplementedError`
guards, and keep any experiments of your own inside the same guard.
