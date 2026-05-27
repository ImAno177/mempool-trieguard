# Agent Guide

## Table of Contents

- [Purpose](#purpose)
- [Documentation Links](#documentation-links)
- [Repository Rules](#repository-rules)
- [Verification](#verification)
- [Current Benchmark Position](#current-benchmark-position)

## Purpose

This file gives AI/code agents enough context to work safely in the Mempool-TrieGuard repository.

## Documentation Links

- [README](README.md) - repo map, benchmark commands, and artifact policy.
- [Setup guide](docs/SETUP.md) - environment setup and local/VPS runbook.
- [Experiment guide](docs/EXPERIMENT_GUIDE.md) - RQ1-RQ4, baselines, metrics, and experimental requirements.
- [Dataset notes](docs/DATASET.md) - local dataset/cache expectations.
- [Progress handoff](docs/PROGRESS.md) - short current status and next improvements.
- [Detector agent notes](internal/AGENTS.md) - Go detector and scoring invariants.
- [Python agent notes](python/AGENTS.md) - benchmark pipeline and full-label replay rules.

## Repository Rules

- Do not commit `.env`, API keys, dRPC URLs with real keys, `results/`, local datasets, Parquet files, generated caches, or binaries.
- Keep production threshold fixed at `tau=0.40` for the current full-dataset benchmark numbers.
- For RQ comparisons, do not tune thresholds per method unless the experiment guide is changed and the report explicitly says so.
- Treat full-label tau sweep results as exploratory calibration analysis, not as replacements for fixed-threshold RQ tables.
- Prefer Go for detector/runtime behavior and Python for dataset processing/statistics.
- Preserve time-aware counterparty logic: trusted counterparties must be valid only if `last_seen <= observed_at` and inside the configured window.

## Verification

Run before publishing changes:

```powershell
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```

## Current Benchmark Position

The current full-label run used all labels in `data/normalized/address_poisoning_ethereum.normalized.full.parquet` locally. Results are not committed because `results/` is ignored.

- Dataset scope: `34,905,969` total rows, `17,365,954` positives, `17,516,047` negatives, `256` shards.
- Fixed-threshold production result at `tau=0.40`: `mempool_trieguard` precision `0.999214`, recall `0.957280`, F1 `0.977797`.
- Full-label aggregate RQ2 lookup used in the current manuscript: `mempool_trieguard` mean `0.003023` ms, p95 `0.000000` ms, p99 `0.000092` ms vs `linear_scan` mean `0.143894` ms, p95 `1.070443` ms, p99 `2.258818` ms.
- Additional per-wallet RQ2 scaling and overhead artifacts are local at `results/missing_experiments_20260523`: at `10,000` counterparties, `mempool_trieguard` lookup mean `0.000668` ms vs `linear_scan` `0.211963` ms; load/update mean `6.491340` ms; heap `160.88` KB per 1,000 counterparties.
- RQ3 caveat: `address_only_trie` reaches F1 `0.978089`, and `no_time` reaches F1 `0.978062`, so the current risk score should be described as needing calibration rather than as dominating every ablation.
- Tau sweep result: `mempool_trieguard` best tau is `0.395` with F1 `0.977826`, only `+0.000029` over tau `0.40`.
- Manuscript files are local-only and excluded from this code repository; current local draft is `paper/paper.tex`.




