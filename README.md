# Mempool-TrieGuard

Mempool-TrieGuard is a Go detector plus Python benchmark pipeline for pre-confirmation Ethereum address-poisoning detection. The detector builds victim-specific prefix/suffix tries over trusted counterparties and raises alerts from a risk score over address similarity, transfer type, token context, time decay, and value.

## Table of Contents

- [Repository Map](#repository-map)
- [Setup Guide](docs/SETUP.md)
- [Documentation Links](#documentation-links)
- [What Is Implemented](#what-is-implemented)
- [Security And Secrets](#security-and-secrets)
- [Quick Start](#quick-start)
- [Docker Workflow](#docker-workflow)
- [Full Dataset Benchmark](#full-dataset-benchmark)
- [Full Dataset Tau Sweep](#full-dataset-tau-sweep)
- [RQ2 Scaling And Overhead](#rq2-scaling-and-overhead)
- [Paper Draft](#paper-draft)
- [Artifacts And Dataset Policy](#artifacts-and-dataset-policy)
- [Promote Config Local To VPS](#promote-config-local-to-vps)
- [Verification](#verification)

## Repository Map

| Path | Purpose |
|---|---|
| `cmd/server` | Go Web UI and live detector entrypoint. |
| `cmd/detector-cli` | Go benchmark/replay CLI used by Python. |
| `internal/detector` | Trie retrieval, linear baseline, risk score, unit tests. |
| `internal/bench` | Replay loader, metrics, confusion matrix support for CLI runs. |
| `internal/live` | dRPC pending transaction subscription service. |
| `internal/rpc` | dRPC HTTP/WebSocket helpers and tests. |
| `internal/store` | SQLite persistence for alerts, runs, config versions. |
| `internal/web` | Server-side rendered UI handlers and templates. |
| `python/benchmark_pipeline.py` | Dataset normalization, replay generation, benchmark orchestration, and report artifacts. |
| `scripts/` | Smoke and local helper scripts. |
| `docs/` | Public project notes, experiment guide, and handoff document. |
| `docs/DATASET.md` | How to convert the raw dataset into local Parquet artifacts. |
| `paper/` | Local-only manuscript drafts and paper notes; ignored by Git in this checkout. |

## Documentation Links

- [Setup guide](docs/SETUP.md) - install dependencies, configure env, prepare dataset, run locally/VPS.
- [Root AGENTS.md](AGENTS.md) - instructions for AI/code agents working in this repository.
- [Go detector AGENTS.md](internal/AGENTS.md) - detector invariants, scoring rules, and benchmark expectations.
- [Python benchmark AGENTS.md](python/AGENTS.md) - pipeline and artifact rules.
- [Experiment guide](docs/EXPERIMENT_GUIDE.md) - research questions, baselines, metrics, and required experiments.
- [Dataset notes](docs/DATASET.md) - expected local Parquet/cache artifacts and why they are not committed.
- [Progress handoff](docs/PROGRESS.md) - short status and next work for another student.

## What Is Implemented

- Go detector core with prefix/suffix trie retrieval and risk-score-first alerting.
- Time-aware trusted counterparty filtering: a counterparty must have `last_seen <= observed_at` and be inside `window_days`.
- Baselines/ablations: `confirmed_chain`, `linear_scan`, `address_only_trie`, `prefix_only`, `suffix_only`, `no_token`, `no_time`, `no_value`.
- dRPC live mempool mode using `drpc_pendingTransactions` and HTTP fallback enrichment.
- SSR + HTMX Web UI for local artifacts, live status, provider health, alerts, and config import.
- Python benchmark pipeline for SQL to Parquet normalization, full-label replay, sharding by victim, loss-rate simulation, metrics, and report table generation.
- Full-label tau sweep mode for one-pass threshold analysis across production and ablation methods.
- Local controlled RQ2 scaling and operational-overhead artifacts under `results/missing_experiments_20260523`.

## Security And Secrets

- Never commit `.env`, real dRPC URLs, API keys, generated `results/`, datasets, Parquet files, or binaries.
- Use `.env.example` as the only committed environment template.
- If a key was shared in chat/logs, rotate it in dRPC before deployment.
- Use Basic Auth in VPS mode and set a strong password through environment variables.

## Quick Start

### 1. Environment

```powershell
Copy-Item .env.example .env
# Edit .env locally. Do not commit .env.
```

PowerShell-only alternative:

```powershell
$env:DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_KEY="<YOUR_KEY>"
$env:APP_BASIC_AUTH_USER="admin"
$env:APP_BASIC_AUTH_PASS="change-me"
```

### 2. Smoke test dRPC HTTP

```powershell
python scripts\smoke_drpc.py
```

### 3. Build and test

```powershell
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
python -m py_compile python/benchmark_pipeline.py
```

### 4. Run Web UI / live monitor

```powershell
go run ./cmd/server --config configs\app.yaml
```

Open `http://localhost:8080`.

## Docker Workflow

```powershell
docker compose build
docker compose up -d app
docker compose logs -f app
```

Benchmark smoke profile:

```powershell
docker compose --profile benchmark run --rm benchmark
```

The Docker compose file mounts local `data/` and `results/` for runtime use, but both are ignored by Git.

## Full Dataset Benchmark

The full dataset run uses the local normalized Parquet file and fixed production threshold `tau=0.40`:

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

Expected full-label manifest for the current dataset:

- Total rows: `34,905,969`
- Positives: `17,365,954`
- Negatives: `17,516,047`

Main current result from the last full run:

- `mempool_trieguard`: precision `0.999214`, recall `0.957280`, F1 `0.977797`.
- `linear_scan`: precision `0.978671`, recall `0.965444`, F1 `0.972012`.
- `confirmed_chain`: precision `1.000000`, recall `0.283724`, F1 `0.442033`.
- RQ2 aggregate lookup used in the current manuscript: `mempool_trieguard` mean `0.003023` ms, p95 `0.000000` ms, p99 `0.000092` ms, throughput `191,953.44` TPS, average candidates `2.80`; `linear_scan` mean `0.143894` ms, p95 `1.070443` ms, p99 `2.258818` ms, throughput `35,475.12` TPS, average candidates `97.32`.
- RQ3 caveat: `address_only_trie` reaches F1 `0.978089`, slightly above the current full risk score. Treat this as a risk-score calibration finding, not as evidence to tune thresholds per method for RQ comparisons.

See [Progress handoff](docs/PROGRESS.md) for interpretation and next improvements.

## Full Dataset Tau Sweep

Use tau sweep only as an exploratory calibration analysis. RQ tables should still use the fixed production threshold `tau=0.40` unless the experiment protocol is explicitly changed.

```powershell
python -u python/benchmark_pipeline.py `
  --full-label-tau-sweep `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet `
  --full-label-source-results-dir results/full_label_full_dataset_YYYYMMDD_tau040 `
  --results-dir results/full_label_tau_sweep_YYYYMMDD `
  --detector-cli .\detector-cli.exe `
  --token-cache results/rpc_cache/full_dataset_token_metadata_cache.json `
  --no-rpc-enrich `
  --loss-rates 0 `
  --benchmark-runs 1 `
  --jobs 12
```

The current completed sweep used loss rate `0`, all full-label shards, and replay delays `5/15/30` seconds. Key results:

| Method | Best tau | Best F1 | F1 at tau=0.40 | Delta F1 |
|---|---:|---:|---:|---:|
| `address_only_trie` | 0.505 | 0.978172 | 0.978089 | +0.000083 |
| `mempool_trieguard` | 0.395 | 0.977826 | 0.977797 | +0.000029 |
| `no_time` | 0.430 | 0.978073 | 0.978062 | +0.000011 |
| `no_token` | 0.335 | 0.976106 | 0.973297 | +0.002809 |
| `no_value` | 0.430 | 0.977976 | 0.977869 | +0.000107 |
| `prefix_only` | 0.390 | 0.977967 | 0.977916 | +0.000050 |
| `suffix_only` | 0.390 | 0.978000 | 0.977945 | +0.000054 |

Interpretation: `tau=0.40` is nearly optimal for `mempool_trieguard`, but the current contextual risk score still does not dominate every ablation.

## RQ2 Scaling And Overhead

The strict per-wallet RQ2 scaling and operational-overhead experiment has been completed locally under `results/missing_experiments_20260523`. This directory is ignored by Git.

Scope: shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, `63,607` available trusted counterparties, `10,000` replay events, delay `15` seconds, loss rate `0`, and `30` runs per method/size.

| Method | Counterparties | Lookup mean ms | Std | Throughput TPS |
|---|---:|---:|---:|---:|
| `mempool_trieguard` | 10 | 0.000306 | 0.000124 | 1,470,579.88 |
| `linear_scan` | 10 | 0.000553 | 0.000145 | 1,015,607.87 |
| `mempool_trieguard` | 100 | 0.000382 | 0.000153 | 1,330,953.84 |
| `linear_scan` | 100 | 0.002698 | 0.000211 | 316,276.11 |
| `mempool_trieguard` | 1,000 | 0.000543 | 0.000159 | 1,115,330.47 |
| `linear_scan` | 1,000 | 0.021775 | 0.000377 | 44,908.07 |
| `mempool_trieguard` | 10,000 | 0.000668 | 0.000175 | 903,593.11 |
| `linear_scan` | 10,000 | 0.211963 | 0.001097 | 4,696.11 |

Operational overhead at `10,000` counterparties is `6.491340` ms mean load/update time, `1,608.84` KB heap per wallet, and `160.88` KB heap per 1,000 counterparties.

## Paper Draft

The current local IEEEtran draft is `paper/paper.tex`. It reports the full-label run, aggregate RQ2 lookup p95/p99 values, per-wallet RQ2 scaling and overhead, tau sweep, RQ3 calibration caveat, replay limitation using `observed_at = block_time - delay`, and a code/data availability statement pointing to this repository. The `paper/` directory is ignored by Git in this checkout, so manuscript files and PDFs are local artifacts unless the repository policy changes.

## Artifacts And Dataset Policy

Ignored by Git:

- `results/` benchmark outputs.
- `paper/` local manuscript drafts in this checkout.
- `29212703/` extracted dataset and `29212703.zip`.
- `data/` entirely; recreate local Parquet artifacts from the raw SQL dump when needed.
- Local binaries such as `detector-cli.exe` and `server.exe`.
- `.env` and any `.env.*` files except `.env.example`.

Commit only code, config templates, and public documentation.

## Promote Config Local To VPS

1. Run benchmark locally.
2. Use `best_config.yaml` from the chosen result directory.
3. In the VPS UI, import the config from `/config`.
4. Start live mode from the dashboard.

## Verification

Before opening a PR or publishing results, run:

```powershell
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```




