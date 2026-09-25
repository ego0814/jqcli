# Step 5.5 Derived Financial Components

This local-only namespace derives ROA, gross profit margin, and asset turnover
from the Step 5.4 panel. It retains the original `fina_indicator` values for
reference and records cross-source divergence at a 1% threshold.

The layer is fail-closed: missing or conflicting inputs produce null derived
values, no imputation or unit conversion is performed, and the layer does not
calculate F-score, FAR, beta, or returns.

Run from the repository root:

```text
python -m research.financial_data_repair_v2.step5_5_derived_components.main
```
