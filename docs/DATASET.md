# Dataset Preparation

## Table of Contents

- [Related Docs](#related-docs)
- [What Is Not In Git](#what-is-not-in-git)
- [Expected Local Layout](#expected-local-layout)
- [Convert SQL To Parquet](#convert-sql-to-parquet)
- [Reuse RPC Caches](#reuse-rpc-caches)
- [Full-Label Benchmark Input](#full-label-benchmark-input)

## Related Docs

- [README](../README.md) - setup, Docker, and benchmark commands.
- [Experiment guide](EXPERIMENT_GUIDE.md) - RQ1-RQ4 and required metrics.
- [Progress handoff](PROGRESS.md) - current status and next work.
- [Root AGENTS](../AGENTS.md) - repository rules for AI/code agents.

## What Is Not In Git

The repository intentionally does not include dataset files:

- `data/`
- `29212703/`
- `29212703.zip`
- Parquet files
- dRPC metadata/history caches
- benchmark outputs under `results/`

These files are large and may contain local run artifacts. Keep them on disk only.

## Expected Local Layout

Use this local layout when running the full benchmark:

```text
29212703/
  address_poisoning_ethereum.sql/
    address_poisoning_ethereum.sql

data/
  normalized/
    address_poisoning_ethereum.normalized.full.parquet

results/
  rpc_cache/
    full_dataset_token_metadata_cache.json
```

Only code and docs are pushed to GitHub.

## Convert SQL To Parquet

Normalize the SQL dump once, then reuse the Parquet file for all benchmark runs:

```powershell
python python/benchmark_pipeline.py `
  --dataset-root 29212703 `
  --normalize-only `
  --max-rows 0 `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet
```

Expected full Parquet metadata for the current dataset:

- Rows: `34,905,969`
- Format: Parquet
- Main file: `data/normalized/address_poisoning_ethereum.normalized.full.parquet`

Do not reparse the SQL dump for every experiment. Use `--dataset-cache` to point to the normalized Parquet artifact.

## Reuse RPC Caches

If token metadata was already fetched from dRPC, reuse the cache:

```powershell
--token-cache results/rpc_cache/full_dataset_token_metadata_cache.json
```

Do not repeatedly fetch dRPC metadata unless the cache is missing or intentionally refreshed.

## Full-Label Benchmark Input

The full-label run should use the local Parquet file:

```powershell
python -u python/benchmark_pipeline.py `
  --full-label-replay `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet `
  --max-rows 0 `
  --shard-count 256 `
  --shard-batch-size 4 `
  --results-dir results/full_label_full_dataset_YYYYMMDD_tau040 `
  --detector-cli .\detector-cli.exe `
  --token-cache results/rpc_cache/full_dataset_token_metadata_cache.json `
  --no-rpc-enrich `
  --loss-rates 0,0.10,0.25,0.50 `
  --tau-grid 0.40 `
  --benchmark-runs 1 `
  --jobs 12
```
