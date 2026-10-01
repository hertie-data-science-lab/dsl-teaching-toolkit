# Writing an assignment: derived or by hand

An assignment template has two branches. `main` is the starter students receive at hand
out. `solution` holds the model answer, `grading_config.yml` and any hidden tests; students
see it only after the cutoff.

You choose, per template, how `main` is written. The choice is `starter:` in
`grading_config.yml`, and **How it is marked → Starter** in the template's settings.

| `starter:` | You write | `main` comes from |
|---|---|---|
| `derived` | the `solution` branch only, with each answer marked | **Derive student version**, which blanks the marked answers |
| `handwritten` | `main` (skeleton, brief) and `solution`, separately | you; nothing is derived |

**Default.** New assignment asks. With tests on it suggests `derived`, so the tests always
match the starter; with tests off, `handwritten`. Change it either way, at any time.

A template made before the key existed reads as `derived` when any file under `solution/`
on its `solution` branch carries a marker, else `handwritten`. The migration writes the key.
Until it does, the course page reads such a template as derived whenever `solution/` holds
a notebook or script.

## Derived from your solution

Write **one** notebook (or script, Rmd, qmd, tex) - the one you teach from - in `solution/`
on the `solution` branch, and mark each answer:

```python
def fit(x, y):
    ### BEGIN SOLUTION
    return x @ y
    ### END SOLUTION
```

| Marker | Where | What the student gets |
|---|---|---|
| `### BEGIN SOLUTION` … `### END SOLUTION` | a code cell, `.py`, `.R`, `.Rmd`, `.qmd` | `pass  # YOUR CODE HERE` at the same indent (`# YOUR CODE HERE` outside Python) |
| the cell tag `solution` | a whole notebook cell | the cell's heading, then `_YOUR ANSWER HERE_` |
| `solution=TRUE` | an Rmd/qmd chunk option | the chunk header, with `# YOUR CODE HERE` as its body |
| `% BEGIN SOLUTION` … `% END SOLUTION` | a `.tex` file | `% YOUR ANSWER HERE` |

Then run **Derive student version** (the template's settings, or course org → `.github` →
Actions). It writes `solution/starter.ipynb` onto `main` as `starter.ipynb`, and never
writes to `solution`. Run it after every change to the solution. In the console it runs
straight away; once it ends, **See on GitHub** opens `main`. On the Actions tab `preview`
is on by default and lists the files and counts, never their content.

Derive refuses, rather than publish the answer:

- a file with nothing marked (a blank file such as an empty `__init__.py` is copied as it is);
- a marker that opens and never closes.

It also clears the stored outputs of every code cell it changed: a solution notebook's
outputs are the answers in print.

Only `.ipynb`, `.Rmd`, `.qmd`, `.py`, `.R` and `.tex` are derived. Anything else under
`solution/` stays there.

Each run also writes `.system/starter.json` on `main`: the version of `solution/` it
derived from, and a fingerprint of each file it wrote. Leave it alone; it is how the course
page knows the starter is current.

**Do not edit `main` by hand.** The next Derive overwrites it. The course shows the problem
*main is derived; edit the solution branch and derive again* when a file Derive wrote no
longer matches on `main`.

**Ready** when the brief (`README.md` on `main`) is written and Derive has run since the
last change to `solution/`. Until then the course lists a to-do: *Derive has not been run
yet*, or *The solution changed since the last Derive; derive again*.

## Written by hand

You write `main` yourself: a skeleton, a brief, a different shape from the solution. The
`solution` branch keeps the model answer for marking and for showing students after the
cutoff. Derive is hidden and refuses to run (*This template's starter is written by hand,
so nothing is derived.*). No marker is needed anywhere.

**Ready** when the brief is written and `main` holds something besides it.
