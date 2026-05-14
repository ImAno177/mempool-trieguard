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

The current full-label run used all labels in `data/normalized/address_poisoning_ethereum.normalized.full.parquet` locally. Results are not committed because `results/` is ignored. Manuscript files are local-only and excluded from this code repository.





