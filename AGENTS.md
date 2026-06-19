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
- Keep replay thresholds fixed by the documented protocol: legacy additive baselines use their preselected thresholds, while the current learned LR `mempool_trieguard` row uses validation-selected `tau=0.901`.
- For RQ comparisons, do not tune thresholds on the test set; any per-ablation threshold must be selected on validation and explicitly documented.
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
- Current manuscript detector result uses learned LR score at validation-selected `tau=0.901`: `mempool_trieguard` precision `0.999923`, recall `0.957455`, F1 `0.978229`.
- Current manuscript RQ2 lookup uses the MTG-only LR replay: `mempool_trieguard` mean `0.003891` ms, p95 `0.000000` ms, p99 `0.000000` ms vs legacy `linear_scan` mean `0.143894` ms, p95 `1.070443` ms, p99 `2.258818` ms.
- Additional per-wallet RQ2 scaling and overhead artifacts are local at `results/missing_experiments_20260523`: at `10,000` counterparties, `mempool_trieguard` lookup mean `0.000668` ms vs `linear_scan` `0.211963` ms; load/update mean `6.491340` ms; heap `160.88` KB per 1,000 counterparties.
- RQ3 calibrated LR feature ablation over 30 runs: full address+type+token F1 mean `0.979535`, ahead of address-only `0.979512`, no-type `0.979511`, no-token `0.979513`, suffix-only `0.979511`, and prefix-only `0.978909`. The margin is small and should be reported conservatively.
- Legacy additive tau sweep result: `mempool_trieguard` best tau is `0.395` with F1 `0.977826`, only `+0.000029` over tau `0.40`; this is diagnostic, not the current main scoring formula.
- Manuscript files are local-only and excluded from this code repository; current local draft is `paper/paper.tex`.




