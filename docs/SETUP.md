# Environment Setup

## Table of Contents

- [Related Docs](#related-docs)
- [Prerequisites](#prerequisites)
- [Clone And Install](#clone-and-install)
- [Environment Variables](#environment-variables)
- [Dataset Setup](#dataset-setup)
- [Build And Verify](#build-and-verify)
- [Run Web UI Locally](#run-web-ui-locally)
- [Run Benchmark](#run-benchmark)
- [Run Tau Sweep](#run-tau-sweep)
- [Local Result Artifacts](#local-result-artifacts)
- [Docker And VPS](#docker-and-vps)
- [Common Problems](#common-problems)

## Related Docs

- [README](../README.md) - repository overview and quick commands.
- [Dataset guide](DATASET.md) - how to convert raw SQL into local Parquet.
- [Experiment guide](EXPERIMENT_GUIDE.md) - RQ1-RQ4, baselines, and metrics.
- [Progress handoff](PROGRESS.md) - current result and next improvements.
- [Root AGENTS](../AGENTS.md) - rules for AI/code agents.

## Prerequisites

Recommended versions:

- Go `1.24` or newer.
- Python `3.11` or newer.
- Docker Desktop or Docker Engine, optional but recommended for VPS portability.
- GitHub CLI `gh`, only needed if you want to publish the repository.
- An Ethereum RPC provider. The current code supports dRPC HTTP and WSS endpoints.

Windows PowerShell examples are used below because this project was developed on Windows.

## Clone And Install

```powershell
git clone <REPO_URL> mempool-trieguard
cd mempool-trieguard
python -m pip install -r python\requirements.txt
go mod download
```

## Environment Variables

Create local `.env` from the template:

```powershell
Copy-Item .env.example .env
```

Edit `.env` locally:

```text
APP_BASIC_AUTH_USER=admin
APP_BASIC_AUTH_PASS=change-me
APP_PORT=8080
DRPC_HTTP_URL=https://lb.drpc.live/ethereum/YOUR_KEY
DRPC_WSS_URL=wss://lb.drpc.live/ethereum/YOUR_KEY
DRPC_KEY=YOUR_KEY
```

Do not commit `.env`. It is ignored by Git.

For PowerShell sessions without `.env` loading:

```powershell
$env:DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_KEY="<YOUR_KEY>"
$env:APP_BASIC_AUTH_USER="admin"
$env:APP_BASIC_AUTH_PASS="change-me"
```

## Dataset Setup

The raw dataset is not in Git. Put it locally as:

```text
29212703/
  address_poisoning_ethereum.sql/
    address_poisoning_ethereum.sql
```

Convert SQL to Parquet once:

```powershell
python python/benchmark_pipeline.py `
  --dataset-root 29212703 `
  --normalize-only `
  --max-rows 0 `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet
```

See [Dataset guide](DATASET.md) for the expected local layout and cache policy.

## Build And Verify

```powershell
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```

## Run Web UI Locally

```powershell
go run ./cmd/server --config configs\app.yaml
```

Open:

```text
http://localhost:8080
```

## Run Benchmark

Small smoke run:

```powershell
python python/benchmark_pipeline.py `
  --run-mode smoke `
  --dataset-root 29212703 `
  --config configs/app.yaml `
  --results-dir results/smoke `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet `
  --max-rows 20000 `
  --max-events 500 `
  --benchmark-runs 2 `
  --loss-rates 0,0.25 `
  --tau-grid 0.40 `
  --detector-cli .\detector-cli.exe `
  --no-rpc-enrich
```

Full-label run:

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

## Run Tau Sweep

Use this after a full-label run when you want to analyze threshold sensitivity. This is exploratory; keep RQ comparisons at the fixed production threshold `tau=0.40`.

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

Main outputs:

- `full_label_tau_sweep_report.md`
- `full_label_tau_sweep_best.csv`
- `full_label_tau_sweep_by_method.csv`

## Local Result Artifacts

Current local result directories:

- `results/full_label_daily_rerun_20260525_tau040` - current fixed-threshold full-label replay artifacts used by the manuscript.
- `results/full_label_full_dataset_20260514_tau040` - earlier fixed-threshold full-label replay at `tau=0.40`.
- `results/full_label_tau_sweep_20260523` - exploratory threshold sweep at loss rate `0`.
- `results/missing_experiments_20260523` - strict per-wallet RQ2 lookup scaling plus operational overhead.

The RQ2 scaling artifact uses shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, `10,000` sampled replay events, and `30` runs per method/size. The summary files are `rq2_per_wallet_scaling_summary.csv`, `operational_overhead_summary.csv`, and `missing_experiments_report.md`.

## Docker And VPS

Build image:

```powershell
docker compose build
```

Run app:

```powershell
docker compose up -d app
docker compose logs -f app
```

Run benchmark profile:

```powershell
docker compose --profile benchmark run --rm benchmark
```

On VPS, copy only code plus local runtime directories you intentionally need. Keep `.env` on the VPS only and rotate dRPC keys before production use.

## Common Problems

- `missing DRPC_HTTP_URL`: set `.env` or PowerShell environment variables.
- SQL parsing is slow: convert SQL to Parquet once and reuse `--dataset-cache`.
- Full-label run is slow: increase `--jobs` and use `--shard-batch-size 4`, but watch RAM.
- Tau sweep is slow: pass `--full-label-source-results-dir` from a completed full-label run to reuse shards.
- Docker cannot see dataset: mount local `29212703/`, `data/`, and `results/` as in `docker-compose.yml`.
- LaTeX/PDF generation is not part of the public code repo. The current local draft is `paper/paper.tex`; benchmark CSV/MD artifacts are generated under ignored `results/`.
