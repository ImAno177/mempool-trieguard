# Mempool-TrieGuard

Mempool-TrieGuard is a Go and Python research prototype for pre-confirmation Ethereum address-poisoning detection. It keeps recent trusted counterparties for each protected account in prefix/suffix tries, screens pending ERC-20 transfers, and emits explainable alerts when a candidate lookalike is close to trusted history.

## Current Status

- Live mempool realism: the paper now includes a six-hour dRPC `drpc_pendingTransactions` microbenchmark. The run measured 813,092 pending messages, detector p99 latency of 0.016519 ms, lookup p99 latency of 0.023420 ms, and Telegram Bot API acknowledgment p99 of 956.208 ms.
- Visibility loss: the live run reports a provider-specific public-feed proxy. All included transactions had a 43.003% unseen-pending proxy, while included direct ERC-20 transfer calls had a 24.210% unseen-pending proxy. These numbers describe the observed dRPC feed, not global Ethereum mempool truth or private-order-flow share.
- Risk score: the old hand-weighted additive score was replaced in the manuscript by a lightweight address-gated logistic regression score. The deployed replay row uses validation-selected `tau=0.901`; threshold sweeps are diagnostic only.
- Retrieval baselines: RQ2 now compares trie lookup against linear scan, an in-memory database-index baseline, and a DB-LSH-style candidate-generation baseline.
- Presentation and reproducibility: the manuscript uses `booktabs` tables and a vector TikZ architecture figure. Public code, commands, and the curated paper artifacts live in this repo; raw datasets, full CSV outputs, RPC caches, secrets, and binaries remain untracked.

## Repository Map

| Path | Purpose |
|---|---|
| `cmd/server` | Web UI, live detector, and live microbenchmark entrypoint. |
| `cmd/detector-cli` | Replay benchmark CLI used by the Python pipeline. |
| `internal/detector` | Prefix/suffix trie, baselines, scoring, and detector tests. |
| `internal/live` | dRPC WebSocket/HTTP live pending-feed benchmark code. |
| `internal/bench` | Replay metrics and confusion-matrix helpers. |
| `internal/rpc` | dRPC HTTP/WebSocket helpers. |
| `internal/store` | SQLite persistence for local app state. |
| `internal/web` | Server-rendered UI handlers and templates. |
| `python/benchmark_pipeline.py` | Dataset normalization, replay generation, benchmark orchestration, and reports. |
| `scripts/` | Helper scripts for dRPC smoke checks, active-account generation, VPS runs, and risk training. |
| `configs/app.yaml` | Default runtime configuration. |
| `results/paper_artifacts_20260619/` | Curated JSON/JSONL artifacts for the current manuscript tables. |

## Setup

Prerequisites:

- Go 1.24 or newer.
- Python 3.11 or newer.
- Node.js 20 or newer for some Ethereum helper workflows.
- An Ethereum RPC provider with HTTP and WSS endpoints.

Create local environment variables from the template:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`. A PowerShell session can also set values directly:

```powershell
$env:DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_KEY="<YOUR_KEY>"
$env:APP_BASIC_AUTH_USER="admin"
$env:APP_BASIC_AUTH_PASS="change-me"
```

Install and verify:

```powershell
go mod download
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```

Linux shortcut:

```bash
make verify
make build-linux
```

## Dataset And Replay Benchmark

The raw address-poisoning dataset is not committed. If you have the source dump or local archive, extract or rename it to this layout:

```text
dataset/
  address_poisoning_ethereum.sql/
    address_poisoning_ethereum.sql
data/
  normalized/
    address_poisoning_ethereum.normalized.full.parquet
```

Normalize once:

```powershell
python python/benchmark_pipeline.py `
  --dataset-root dataset `
  --normalize-only `
  --max-rows 0 `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet
```

Run a full-label replay:

```powershell
python -u python/benchmark_pipeline.py `
  --full-label-replay `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet `
  --max-rows 0 `
  --shard-count 256 `
  --shard-batch-size 4 `
  --results-dir results/full_label_full_dataset_YYYYMMDD `
  --detector-cli .\detector-cli.exe `
  --token-cache results/rpc_cache/full_dataset_token_metadata_cache.json `
  --no-rpc-enrich `
  --loss-rates 0,0.10,0.25,0.50 `
  --tau-grid 0.40 `
  --benchmark-runs 1 `
  --jobs 12
```

Protocol notes:

- Legacy additive baselines keep documented fixed thresholds.
- The current learned LR `mempool_trieguard` replay row uses validation-selected `tau=0.901`.
- Do not tune thresholds on the test set.
- Treat full-label tau sweeps as calibration diagnostics, not replacements for fixed-protocol RQ tables.

## Live Mempool Microbenchmark

Build a recent active protected-account file:

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

Run the live benchmark:

```powershell
$env:APP_PROTECTED_ACCOUNTS_PATH="results\live_active_protected_accounts_24h_1000victims.json"

go run ./cmd/server --config configs\app.yaml `
  --live-benchmark-duration 6h `
  --live-benchmark-out results\live_mempool_YYYYMMDD_HHMM
```

Expected artifacts:

- `live_mempool_metrics.json`
- `run_manifest.json`
- `live_mempool_events.csv`
- `live_mempool_blocks.csv`
- `live_mempool_alerts.jsonl`

The collector excludes the first 60 seconds or first 5 observed blocks from visibility denominators. Live visibility is computed as `1 - included_seen_pending_rate` for all included transactions and `1 - included_erc20_seen_pending_rate` for included direct ERC-20 transfer-call transactions. Telegram timing is a Bot API `sendMessage` acceptance proxy, not device delivery or read-receipt timing.

## Current Paper Artifact Bundle

The committed artifact folder is intentionally small:

```text
results/paper_artifacts_20260619/
  paper_results_summary.json
  source_hashes.json
  live_mempool_20260619T0316_1000v/
    live_mempool_metrics.json
    run_manifest.json
    live_mempool_alerts.jsonl
  protected_accounts/
    live_active_protected_accounts_24h_1000victims.json.manifest.json
  rq3_lr_feature_ablation/
    summary_aggregate.json
```

It contains JSON/JSONL evidence for the manuscript tables and live alerts. It does not include large CSV event/block streams, raw datasets, Parquet files, RPC caches, full protected-account counterparty lists, binaries, or secrets.

## Security And Artifact Policy

Do not commit:

- `.env`, API keys, real dRPC URLs with embedded keys, Telegram bot tokens, or passwords.
- `data/`, `dataset/`, `dataset.zip`, Parquet files, or local dataset caches.
- Full generated `results/` directories except `results/paper_artifacts_20260619/`.
- Local binaries such as `detector-cli.exe`, `server.exe`, DLLs, shared objects, and release builds.
- Local manuscript drafts under `paper/` unless the repository policy is changed explicitly.

## Current Headline Results

- Dataset scope: 34,905,969 total rows, 17,365,954 positives, 17,516,047 negatives, 256 shards.
- RQ1 current LR replay row: precision 0.999923, recall 0.957455, F1 0.978229.
- RQ2 full replay: `mempool_trieguard` mean lookup 0.003565 ms vs `linear_scan` 0.143894 ms; the controlled 10,000-counterparty setting reports 0.001443 ms for trie lookup.
- RQ3 calibrated LR ablation: full address+type+token F1 mean 0.979535, only slightly above address-only 0.979512.
- RQ4 simulated pending loss: recall falls from 0.957455 at 0% loss to 0.478557 at 50% loss.
- Live supplement: 813,092 pending messages over six hours, detector p99 0.016519 ms, lookup p99 0.023420 ms, alert acknowledgment p99 956.208 ms, provider-specific visibility loss of 43.003% for all included transactions and 24.210% for included ERC-20 transfer calls.
