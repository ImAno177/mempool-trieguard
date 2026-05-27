# Python Benchmark Agent Guide

## Table of Contents

- [Scope](#scope)
- [Documentation Links](#documentation-links)
- [Pipeline Rules](#pipeline-rules)
- [Full-Label Replay](#full-label-replay)
- [Full-Label Tau Sweep](#full-label-tau-sweep)
- [Local Result Artifacts](#local-result-artifacts)

## Scope

`benchmark_pipeline.py` owns dataset normalization, replay generation, detector orchestration, aggregation, and benchmark artifact export.

## Documentation Links

- [Root README](../README.md) - commands and artifact policy.
- [Experiment guide](../docs/EXPERIMENT_GUIDE.md) - RQ definitions.
- [Dataset notes](../docs/DATASET.md) - local Parquet/cache expectations.
- [Progress handoff](../docs/PROGRESS.md) - next work.

## Pipeline Rules

- Do not fetch dRPC data repeatedly if a cache is available.
- Do not use poisoning/lookalike rows as trusted counterparties.
- For full-label runs, keep `tau=0.40` fixed for all compared methods.
- Do not use per-method best tau values from tau sweep in RQ tables unless the experiment guide is explicitly changed.
- Preserve output schemas for `metrics.csv`, `ablation.csv`, `stats.json`, `table_for_paper.md`, and `best_config.yaml` when touching legacy paths.

## Full-Label Replay

- Use `--full-label-replay` for full-dataset results.
- Positives are `zero_value_transfer OR tiny_transfer OR counterfeit_token_transfer`.
- Negatives are valid `intended_transfer` rows excluding poisoning and payoff rows.
- Shard by victim to avoid loading all rows into RAM.
- Aggregate TP/FP/FN/TN over all shards and replay delay profiles.

## Full-Label Tau Sweep

- Use `--full-label-tau-sweep` for exploratory threshold analysis.
- Prefer `--full-label-source-results-dir` from a completed `--full-label-replay` run so shards can be reused.
- Default sweep grid is `0.000` to `1.000` in `0.005` increments.
- Default swept methods are `mempool_trieguard,address_only_trie,prefix_only,suffix_only,no_token,no_time,no_value`.
- Main outputs are `full_label_tau_sweep_report.md`, `full_label_tau_sweep_best.csv`, and `full_label_tau_sweep_by_method.csv`.
- Current interpretation: `tau=0.40` is nearly optimal for `mempool_trieguard`, but address-only and no-time ablations slightly exceed the full score, so risk-score changes need held-out validation.

## Local Result Artifacts

- Current fixed-threshold full-label artifacts used by the manuscript: `results/full_label_daily_rerun_20260525_tau040`.
- Earlier fixed-threshold full-label run: `results/full_label_full_dataset_20260514_tau040`.
- Exploratory tau sweep: `results/full_label_tau_sweep_20260523`.
- Per-wallet RQ2 scaling and operational overhead: `results/missing_experiments_20260523`.
- The missing-experiment artifact is local and ignored by Git; its headline 10,000-counterparty result is `mempool_trieguard` lookup mean `0.000668` ms versus `linear_scan` `0.211963` ms.



