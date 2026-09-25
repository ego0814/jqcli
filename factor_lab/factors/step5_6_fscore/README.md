# Step 5.6 F-score Panel

This namespace builds the approved eight-component F-score panel from the
Step 5.5 derived panel. F1-F6, F8 and F9 are the only scored components.
`F7_PROXY` is retained as a separate diagnostic and is never included in the
total score.

The builder is local-only, deterministic, no-imputation and fail-closed. A
missing or conflicting input makes only its component unavailable or
conflicted; an incomplete row retains all available component results and has
`aggregated.total_score_8 = null`. Prior annual data must be present and
visible by the row's rebalance date.

Run from the repository root:

```text
python -m research.financial_data_repair_v2.step5_6_fscore.main
```

The six evidence files are written under the Step 5.6 evidence namespace.
