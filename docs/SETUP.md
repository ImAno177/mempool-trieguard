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

Reviewer/Linux build:

```bash
make verify
make build-linux
```

The Linux server binary is `dist/server-linux-amd64`. GitHub Actions builds the same binary on push and publishes it as a release asset when a `v*` tag is pushed.

## Run Web UI Locally

```powershell
go run ./cmd/server --config configs\app.yaml
```

Open:

```text
http://localhost:8080
```

## Run Live Mempool Micro-Benchmark

This is the run mode for the reviewer-requested live mempool experiment. It does not replace the full replay benchmark. It measures provider-facing pending-feed behavior and detector latency on real pending messages.

For a live run, prefer a protected-account file built from recent direct ERC-20 calldata rather than the old replay asset. This matches the live parser, which decodes pending `transfer(address,uint256)` and `transferFrom(address,address,uint256)` calldata. The helper below scans recent full blocks, aggregates bidirectional counterparties, filters selected victim accounts with `eth_getCode` by default, enriches token metadata up to a capped limit, and writes a manifest with the block range and hashes:

```powershell
python scripts\build_active_protected_accounts.py `
  --lookback-blocks 7200 `
  --batch-size 3 `
  --max-victims 1000 `
  --min-counterparties 5 `
  --max-counterparties-per-victim 100 `
  --max-rows 100000 `
  --metadata-limit 400 `
  --contract-probe-limit 3000 `
  --sleep-ms 25 `
  --out results\live_active_protected_accounts_24h_1000victims.json
```

dRPC's current free-tier documentation lists 210M CU per 30 days, a usual `120,000` CU/min/IP limit, possible regional reduction to `50,400` CU/min, and a JSON-RPC batch cap of `3` items. Keep `--batch-size 3` or lower on free tier, and avoid repeated smoke subscriptions in a tight loop.

Prepare environment variables on the VPS:

```powershell
$env:DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_KEY="<YOUR_KEY>"
$env:APP_PROTECTED_ACCOUNTS_PATH="results\live_active_protected_accounts_24h_1000victims.json"
```

Run one 6-hour collection:

```powershell
.\server.exe --config configs\app.yaml `
  --live-benchmark-duration 6h `
  --live-benchmark-out results\live_mempool_YYYYMMDD_HHMM
```

Linux/VPS shell equivalent:

```bash
export DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
export DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
export DRPC_KEY="<YOUR_KEY>"
export APP_PROTECTED_ACCOUNTS_PATH="results/live_active_protected_accounts_24h_1000victims.json"

./server --config configs/app.yaml \
  --live-benchmark-duration 6h \
  --live-benchmark-out results/live_mempool_YYYYMMDD_HHMM
```

Expected artifacts:

- `live_mempool_metrics.json` - summary for the paper table.
- `run_manifest.json` - provider host, region/VPS hint, Go/runtime metadata, git revision when available, config hash, and protected-account hash.
- `live_mempool_events.csv` - one row per pending-feed message.
- `live_mempool_blocks.csv` - one post-warmup row per included block.
- `live_mempool_alerts.jsonl` - emitted alerts, if any, with Telegram `sendMessage` receipt metadata when Telegram is configured.

Run protocol for paper artifacts:

- Run one 2-minute smoke collection first to verify credentials and artifact creation, then wait a few minutes before the final run to avoid provider subscription rate limits.
- Preferred final run for the reviewer supplement in this checkout: one continuous 6-hour VPS run with the 1000-victim recent protected-account file above. Keep the 50-victim file only as a smaller audit/control artifact; it is usually too sparse for observing real alerts in a short window.
- A longer 48-hour VPS run remains acceptable if quota and review time allow; report the chosen duration exactly.
- If the 6-hour run fails, fall back to 3 independent 30-minute collections at different UTC windows and report the shorter protocol explicitly.
- Set `LIVE_BENCHMARK_REGION` or `VPS_REGION` if you want the manifest to record the deployment region.
- Accept a visibility run only when `visibility_valid=true`. This requires warmup completion, at least 100 post-warmup blocks, and `subscription_dropped_messages=0`.

The long-run collector retains pending-hash and sender/nonce state for 6 hours to bound memory during long runs, flushes CSV artifacts every 30 seconds, and records WebSocket reconnects in `subscription_reconnects` and `subscription_ids`. Report detector latency from `detector_latency_*` and `lookup_latency_*`, including p50/p95/p99 and the `*_us` or `*_ns` fields when discussing timer resolution. Report provider/enrichment overhead separately from `fetch_latency_*`. Telegram alert timing is measured as a Bot API acceptance proxy using `telegram_send_latency_*`, `detector_to_telegram_accept_*`, `pending_to_telegram_accept_*`, and per-alert `message_id`/API `date` in `live_mempool_alerts.jsonl`; Telegram does not expose end-user device notification or read-receipt timing through the bot API. Use `pending_messages_per_second`, `pending_interarrival_*`, and `pending_to_block_timestamp_lead_*` to compare detector latency against live feed pressure. Use `included_visibility_loss_rate` and `included_erc20_visibility_loss_rate` only as provider-specific public-pending-feed visibility-loss proxies. Do not report live precision/recall from this run, and do not equate unseen included transactions with private order flow alone.

### VPS Pull From GitHub Release

After pushing a tag such as `v0.1.0-live`, GitHub Actions publishes `server-linux-amd64`. On the VPS:

```bash
cd ~
curl -fsSL https://raw.githubusercontent.com/ImAno177/mempool-trieguard/main/scripts/vps_install_release.sh -o vps_install_release.sh
bash vps_install_release.sh

cd ~/mempool-trieguard
mkdir -p configs results logs data
curl -fsSL https://raw.githubusercontent.com/ImAno177/mempool-trieguard/main/configs/app.yaml -o configs/app.yaml
curl -fsSL https://raw.githubusercontent.com/ImAno177/mempool-trieguard/main/scripts/vps_run_live.sh -o vps_run_live.sh
chmod +x vps_run_live.sh
```

Keep `.env` and `results/live_active_protected_accounts_24h_1000victims.json` on the VPS, not in Git. Start the run:

```bash
LIVE_BENCHMARK_DURATION=6h ./vps_run_live.sh
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

Use this after a full-label run when you want to analyze threshold sensitivity. This is exploratory for the legacy additive score; current LR detector results must use validation-selected thresholds and no test-set tuning.

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

- `results/full_label_lr_mtg_only_20260616` - current MTG-only learned-LR full-label replay artifacts used by the manuscript.
- `results/lr_feature_ablation_canonical_split_victim_30run_20260616` - current 30-run LR feature-ablation artifacts used by the manuscript.
- `results/full_label_daily_rerun_20260525_tau040` - legacy fixed-threshold full-label replay artifacts.
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
