# Step 5.5 v2 Derived Components

Derives three ratio components from the Step 5.4 v2 panel (153,228 OK rows):

- `roa_derived = n_income / ((total_assets_t_minus_1 + total_assets_t) / 2)`
- `grossprofit_margin_derived = (revenue - oper_cost) / revenue`
- `assets_turn_derived = revenue / total_assets_t`

The original Step 5.5 contract is reused verbatim by importing its calculator,
comparator, schema and `PriorPeriodResult` read-only: dual-source retention
(derived plus the `fina_indicator` reference), a 1% cross-source divergence
threshold, `UNAVAILABLE` when any input is null, and `CONFLICT` when any input is
conflicted. No unit conversion and no imputation are applied.

Both passes stream the 1.45 GB panel instead of loading it, and the large outputs
(panel, drop log, cross-check) are written incrementally while their fingerprints
are computed on the fly, keeping the memory peak well below the panel size.
