# Mempool-TrieGuard

Mempool-TrieGuard is a Go detector plus Python benchmark pipeline for pre-confirmation Ethereum address-poisoning detection. The detector builds victim-specific prefix/suffix tries over trusted counterparties and raises alerts from a risk score over address similarity, transfer type, token context, time decay, and value.

## Table of Contents

- [Repository Map](#repository-map)
- [Setup Guide](docs/SETUP.md)
- [Documentation Links](#documentation-links)
- [What Is Implemented](#what-is-implemented)
- [Security And Secrets](#security-and-secrets)
- [Quick Start](#quick-start)
- [Live Mempool Micro-Benchmark](#5-run-live-mempool-micro-benchmark)
- [Release Binary And VPS Pull](#6-release-binary-and-pull-from-vps)
- [Docker Workflow](#docker-workflow)
- [Full Dataset Benchmark](#full-dataset-benchmark)
- [Full Dataset Tau Sweep](#full-dataset-tau-sweep)
- [Colab Learned Risk Training](#colab-learned-risk-training)
- [Q1 Paper Retrieval Baselines](#q1-paper-retrieval-baselines)
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
| `.github/workflows/` | Linux binary build and release workflows for VPS deployment. |
| `Makefile` | Reviewer-friendly verify/build targets. |
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
- Baselines/ablations: `confirmed_chain`, `linear_scan`, `db_index`, `dblsh2_display`, `address_only_trie`, `prefix_only`, `suffix_only`, `no_token`, `no_time`, `no_value`.
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

Linux/reviewer equivalent:

```bash
make verify
make build-linux
```

The Linux server binary is written to `dist/server-linux-amd64`.

### 4. Run Web UI / live monitor

```powershell
go run ./cmd/server --config configs\app.yaml
```

Open `http://localhost:8080`.

### 5. Run live mempool micro-benchmark

Use this mode for the reviewer-facing live-mempool experiment. It subscribes to the pending feed, enriches hash-only payloads with `eth_getTransactionByHash`, runs the detector, walks full blocks sequentially after a warmup period, and writes artifacts for latency and pending-feed visibility analysis.

Build a recent active protected-account file first. The helper scans recent full blocks and decodes the same direct ERC-20 calldata selectors used by the live pending parser:

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

```powershell
$env:APP_PROTECTED_ACCOUNTS_PATH="results\live_active_protected_accounts_24h_1000victims.json"
$env:DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_KEY="<YOUR_KEY>"

go run ./cmd/server --config configs\app.yaml `
  --live-benchmark-duration 6h `
  --live-benchmark-out results\live_mempool_YYYYMMDD_HHMM
```

Artifacts:

- `live_mempool_metrics.json`: summary metrics for paper tables.
- `run_manifest.json`: provider host, VPS/region hint if set, Go/runtime metadata, git revision when available, config hash, and protected-account file hash.
- `live_mempool_events.csv`: per pending-message fetch, decode, detector, lookup, inter-arrival, sender/nonce, fee, candidate, replacement, and alert timings.
- `live_mempool_blocks.csv`: one post-warmup row per included block with how many included transactions were observed first in the pending feed.
- `live_mempool_alerts.jsonl`: one row per emitted alert, including Telegram `sendMessage` receipt metadata when configured (`message_id`, API `date`, local send latency, detector-to-Telegram acceptance proxy, and pending-to-Telegram acceptance proxy).

The benchmark excludes the first 60 seconds or first 5 observed blocks, whichever is longer. Treat the visibility fields as valid only when `visibility_valid=true`, which requires warmup completion, at least 100 post-warmup blocks, and `subscription_dropped_messages=0`.

For the VPS supplement in this checkout, run a 2-minute smoke test, wait a few minutes to avoid provider subscription rate limits, then run one continuous 6-hour collection with the 1000-victim recent protected-account file above. The smaller 50-victim file is useful for manual auditability, but it is usually too sparse to observe real live alerts in a short window. A 48-hour collection is still acceptable if quota and review time allow. The collector retains pending-hash and sender/nonce state for 6 hours to bound memory, flushes CSV artifacts every 30 seconds, and records WebSocket reconnects in `subscription_reconnects` and `subscription_ids`.

For the paper, report detector latency separately from provider enrichment latency. Telegram alert timing should be reported as a Bot API acceptance proxy: `sendMessage` HTTP round-trip plus Telegram `message_id`/server `date`; the Bot API does not expose end-user device notification or read-receipt timing. The visibility-loss proxy is `1 - included_seen_pending_rate` for all included transactions, and `1 - included_erc20_seen_pending_rate` for ERC-20 transfer-call transactions seen in counted blocks. This is provider-specific public-feed visibility, not global Ethereum mempool ground truth, and it does not produce live precision/recall labels.

### 6. Release binary and pull from VPS

Do not commit binaries to Git. Push a tag and let GitHub Actions publish the Linux binary as a release asset:

```powershell
git tag v0.1.0-live
git push origin v0.1.0-live
```

On the VPS:

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

Keep `.env` and the generated protected-account file on the VPS. Then run:

```bash
LIVE_BENCHMARK_DURATION=6h ./vps_run_live.sh
```

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

The legacy additive full dataset run uses the local normalized Parquet file and fixed production threshold `tau=0.40`:

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

Main current result used by the manuscript:

- `mempool_trieguard` with the learned LR score at validation-selected `tau=0.901`: precision `0.999923`, recall `0.957455`, F1 `0.978229`.
- `linear_scan`: precision `0.978671`, recall `0.965444`, F1 `0.972012`.
- `confirmed_chain`: precision `1.000000`, recall `0.283724`, F1 `0.442033`.
- RQ2 aggregate lookup used in the current manuscript: `mempool_trieguard` LR mean `0.003891` ms, p95 `0.000000` ms, p99 `0.000000` ms, throughput `158,954.74` TPS, average candidates `2.80`; `linear_scan` mean `0.143894` ms, p95 `1.070443` ms, p99 `2.258818` ms, throughput `35,475.12` TPS, average candidates `97.32`.
- RQ3 calibrated LR feature ablation over 30 runs: full address+type+token F1 mean `0.979535`, ahead of address-only `0.979512`, no-type `0.979511`, no-token `0.979513`, prefix-only `0.978909`, and suffix-only `0.979511`.

See [Progress handoff](docs/PROGRESS.md) for interpretation and next improvements.

## Full Dataset Tau Sweep

Use tau sweep only as an exploratory calibration analysis. The current manuscript protocol has explicitly changed for the main detector row: legacy additive baselines keep their fixed thresholds, while the learned LR `mempool_trieguard` row uses validation-selected `tau=0.901`.

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

Interpretation: `tau=0.40` was nearly optimal for the old additive `mempool_trieguard` score, but feature calibration required the learned LR score now used in the main detector row.

## Colab Learned Risk Training

The reviewer-facing learned-risk experiment is prepared as a Colab package rather than trained in this repository. Export one best-candidate feature row per canonical replay event (`delay=15`, `loss=0`) from the full-label shards:

```powershell
python scripts\export_risk_training_dataset.py `
  --shards-dir results\full_label_full_dataset_20260514_tau040\full_label_shards `
  --source-manifest results\full_label_full_dataset_20260514_tau040\full_label_manifest.json `
  --dataset-cache data\normalized\address_poisoning_ethereum.normalized.full.parquet `
  --token-metadata results\rpc_cache\full_dataset_token_metadata_cache.json `
  --out-dir results\colab_risk_training_full_YYYYMMDD `
  --delay-seconds 15 `
  --jobs 8
```

For a one-shard smoke test, add `--shards 0` or `--max-shards 1`. Upload the exported `features/` directory plus `manifest.json`, `splits.json`, `feature_schema.json`, `source_hashes.json`, and `notebooks/train_risk_full_dataset_colab.ipynb` to Google Drive under `mempool_trieguard_colab/`.

The local training script mirrors the Colab workflow and is useful for smoke tests:

```powershell
python scripts\train_risk_model.py `
  --feature-dir results\colab_risk_training_full_YYYYMMDD\features `
  --out-dir results\learned_risk_model_YYYYMMDD `
  --split-column split_time `
  --skip-rf
```

Thresholds are selected on the validation split only and then frozen for test metrics. Do not report learned-score results in the paper unless the metrics come from held-out test outputs in `metrics.json`.

## Q1 Paper Retrieval Baselines

Reviewer-facing RQ2 comparisons should use stronger indexed retrieval baselines, not only `linear_scan`. The detector CLI includes one traditional DB-index baseline plus one native local reimplementation of a Q1-paper candidate-generation mechanism:

- `db_index`: SQLite in-memory B-tree indexes over materialized `(victim, prefix3..6)` and `(victim, suffix3..6)` columns.
- `dblsh2_display`: DB-LSH-style query-based dynamic bucketing over displayed address nibbles, adapted from Tian, Zhao, and Zhou's ICDE 2022 DB-LSH paper.

Both baselines use the same display vectorization: 6 prefix nibbles plus 6 suffix nibbles, one-hot encoded over 16 hexadecimal values. They replace candidate retrieval only; active-history filtering and exact trusted-recipient suppression remain shared with `mempool_trieguard`. The published baseline rows keep their documented fixed replay thresholds. These are native adaptations for same-workload local benchmarking, not vendored copies of the original DB-LSH C++ artifact or its later journal extensions.

Full-label canonical 15-second replay for the two added baselines:

```powershell
python -u python\benchmark_pipeline.py `
  --config configs\app.yaml `
  --detector-cli .\detector-cli.exe `
  --full-label-replay `
  --full-label-source-results-dir results\full_label_full_dataset_20260514_tau040 `
  --results-dir results\rq2_two_baselines_full_YYYYMMDD `
  --methods db_index,dblsh2_display `
  --delay-profiles 15 `
  --loss-rates 0 `
  --jobs 8
```

Thirty-run controlled per-wallet scaling for the paper table:

```powershell
python scripts\rerun_rq2_scaling.py `
  --source-dir results\missing_experiments_20260523 `
  --out-dir results\rq2_two_baselines_30run_YYYYMMDD `
  --detector-cli .\detector-cli.exe `
  --methods db_index,dblsh2_display `
  --runs 30 `
  --jobs 8
```

Completed local artifacts used by the current draft are under `results/rq2_two_baselines_full_20260615` and `results/rq2_two_baselines_30run_20260615`. The paper combines those rows with the existing `mempool_trieguard` and `linear_scan` RQ2 artifacts; the full-replay table marks the replay scope explicitly.

## RQ2 Scaling And Overhead

The strict per-wallet RQ2 scaling and operational-overhead experiment has been completed locally under `results/missing_experiments_20260523`. Additional 30-run rows for `db_index` and `dblsh2_display` are under `results/rq2_two_baselines_30run_20260615`. These directories are ignored by Git.

Scope: shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, `63,607` available trusted counterparties, `10,000` replay events, delay `15` seconds, loss rate `0`, and `30` runs per method/size.

| Method | Counterparties | Lookup mean ms | Std | Throughput TPS |
|---|---:|---:|---:|---:|
| `mempool_trieguard` | 10 | 0.000306 | 0.000124 | 1,470,579.88 |
| `linear_scan` | 10 | 0.000553 | 0.000145 | 1,015,607.87 |
| `db_index` | 10 | 0.203443 | 0.004186 | 4,854.85 |
| `dblsh2_display` | 10 | 0.017380 | 0.001146 | 52,790.53 |
| `mempool_trieguard` | 100 | 0.000382 | 0.000153 | 1,330,953.84 |
| `linear_scan` | 100 | 0.002698 | 0.000211 | 316,276.11 |
| `db_index` | 100 | 0.203620 | 0.010293 | 4,859.05 |
| `dblsh2_display` | 100 | 0.019871 | 0.001382 | 46,833.94 |
| `mempool_trieguard` | 1,000 | 0.000543 | 0.000159 | 1,115,330.47 |
| `linear_scan` | 1,000 | 0.021775 | 0.000377 | 44,908.07 |
| `db_index` | 1,000 | 0.219756 | 0.012835 | 4,501.59 |
| `dblsh2_display` | 1,000 | 0.048769 | 0.001637 | 19,665.70 |
| `mempool_trieguard` | 10,000 | 0.000668 | 0.000175 | 903,593.11 |
| `linear_scan` | 10,000 | 0.211963 | 0.001097 | 4,696.11 |
| `db_index` | 10,000 | 0.215688 | 0.009550 | 4,581.39 |
| `dblsh2_display` | 10,000 | 0.158263 | 0.011017 | 6,258.47 |

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




