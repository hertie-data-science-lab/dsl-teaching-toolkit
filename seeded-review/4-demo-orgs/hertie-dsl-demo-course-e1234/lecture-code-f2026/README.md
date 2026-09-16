<!-- source: https://github.com/hertie-dsl-demo-course-e1234/lecture-code-f2026/blob/main/README.md @ 749c5d1 -->
# dldemo

Teaching helpers for **Deep Learning (Demo)** (E1234). Deliberately small, pure-Python and
dependency-free, so it installs anywhere and can be read in an afternoon.

```bash
pip install -e .            # from this directory
```

```python
from dldemo import uncertainty

ece = uncertainty.expected_calibration_error(probs, labels)
print(f"accuracy is not the whole story: ECE = {ece:.3f}")
```

## Phased release

Modules are released to the cohort as the term reaches them, so a partial checkout is
normal - which is why `dldemo/__init__.py` imports no submodule eagerly. Each module lands
in the cohort's `materials` repo under `code/`, driven by the `deploy:` entries in that
cohort's `schedule.yml`.

| Module | Released | Contents |
|---|---|---|
| `uncertainty` | session 9 - further topics | entropy, confidence, reliability bins, expected calibration error |
| `audit` | session 10 - policy approaches | per-group selection rate, TPR and FPR, and the four-fifths disparity ratio |
| `serving` | session 11 - deep learning in practice | environment fingerprint, schema check, population stability index, model card |

Everything here is written for legibility, not speed. Use PyTorch and scikit-learn for real
work; use this to see what they are doing.
