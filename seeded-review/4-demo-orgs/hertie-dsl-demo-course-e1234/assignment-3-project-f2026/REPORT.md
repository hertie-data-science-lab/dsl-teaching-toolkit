<!-- source: https://github.com/hertie-dsl-demo-course-e1234/assignment-3-project-f2026/blob/main/REPORT.md @ 3509839 -->
# <Project title>

**Team:** <team name> | **Members:** <names> | **Date:** <date>
**Seed:** <the random seed used everywhere> | **Run with:** `<one command>`

## 1. Question

<What are you predicting or grouping? For whom does the answer matter, and what decision
would change if the answer were different? One paragraph. State the metric you will report
and why it matches that decision.>

## 2. Data

<Source and link. Collection period. Unit of observation. Number of rows and columns after
cleaning. What is missing and how much. Anything you excluded, and why. One paragraph plus a
small table if useful.>

## 3. Method

<Model family and why. Your split scheme - random, grouped, or time-based - and the
dependence structure that justifies it. How hyperparameters were chosen (which grid, which
CV). Name the baseline you are comparing against.>

## 4. Results

<Your metric with an interval, next to the baseline. Disaggregated by any subgroup that
matters. A calibration check if you report probabilities. Two figures at most.>

| Model | Metric | Interval | Notes |
|---|---|---|---|
| Baseline (<what>) | | | |
| <your model> | | | |

## 5. Limitations

<Specifics, not generics. What would break this model? What does the data not cover? Which
result are you least confident in, and why?>

## 6. Model card

- **Intended use:** <the decision it supports, and for whom>
- **Out-of-scope use:** <uses that would be wrong, explicitly>
- **Data:** <source, period, n, known gaps, consent basis>
- **Performance:** <overall, and per subgroup, with intervals>
- **Threshold / decision rule:** <value, and the cost reasoning behind it>
- **Known limitations:** <the failure modes from section 5>
- **Monitoring:** <what would signal drift, checked how often, by whom>

## 7. Contributions

<Two or three lines. Who did what. Be accurate - individual adjustments depend on this and
on the commit history.>

## Reproducing this

```bash
pip install -r requirements.txt
python -m src.pipeline          # or: Rscript src/pipeline.R
```

<Expected runtime. Where outputs are written.>
