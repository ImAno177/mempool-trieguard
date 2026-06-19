# Mempool-TrieGuard

Mempool-TrieGuard is a Go and Python research prototype for pre-confirmation Ethereum address-poisoning detection. It keeps time-aware trusted counterparties for protected accounts, indexes them in prefix/suffix tries, screens pending ERC-20 transfers, and emits explainable alerts for lookalike recipients.

## Repository Map

| Path | Purpose |
|---|---|
| `cmd/server` | JSON control server and live microbenchmark entrypoint. |
| `cmd/detector-cli` | Replay benchmark CLI used by the Python pipeline. |
| `internal/detector` | Trie retrieval, baselines, scoring, and detector tests. |
| `internal/live` | dRPC pending-feed benchmark and live artifact writer. |
| `internal/rpc` | Ethereum HTTP/WebSocket helpers. |
| `python/benchmark_pipeline.py` | Dataset normalization, replay generation, benchmark orchestration, and CSV/JSON reports. |
| `scripts/` | Smoke checks, active-account generation, VPS helpers, and risk-training utilities. |
| `configs/app.yaml` | Default runtime configuration. |
| `results/paper_artifacts_20260619/` | Committed paper artifact bundle. |

## Setup

Install Go 1.24+, Python 3.11+, and an Ethereum RPC provider with HTTP and WSS endpoints. Create local secrets from the template, then verify the build:

```powershell
Copy-Item .env.example .env

go mod download
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```

Set runtime secrets only through `.env` or the shell. Do not commit real RPC URLs, API keys, Telegram tokens, or passwords.

```powershell
$env:DRPC_HTTP_URL="https://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:DRPC_WSS_URL="wss://lb.drpc.live/ethereum/<YOUR_KEY>"
$env:APP_BASIC_AUTH_USER="admin"
$env:APP_BASIC_AUTH_PASS="change-me"
```

Linux shortcut:

```bash
make verify
make build-linux
```

## Dataset

The replay benchmark uses the public [Blockchain Address Poisoning (Companion Dataset)](https://kilthub.cmu.edu/articles/dataset/Blockchain_Address_Poisoning_Companion_Dataset_/29212703) from KiltHub/Figshare, DOI `10.1184/R1/29212703`. Download `address_poisoning_ethereum.sql.gz` from that record and extract it into this local layout:

```text
dataset/
  address_poisoning_ethereum.sql/
    address_poisoning_ethereum.sql
  payoff_transfers_ethereum.csv
  payoff_transfers_bsc.csv
```

The raw SQL dump is about 10 GB after decompression and is intentionally ignored by Git.

Convert the SQL dump to the local Parquet cache used by the pipeline:

```powershell
python python/benchmark_pipeline.py `
  --dataset-root dataset `
  --normalize-only `
  --max-rows 0 `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet
```

Expected full-cache scope for the current paper run: `34,905,969` rows, `17,365,954` positive poisoning labels, `17,516,047` benign intended-transfer negatives, and `256` victim shards.

## Replay Benchmark

Build the detector CLI, then run the full-label replay from the Parquet cache:

```powershell
go build -o detector-cli.exe ./cmd/detector-cli

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

Protocol rules:

- Legacy additive baselines keep their fixed thresholds.
- The current `mempool_trieguard` paper row uses the learned LR score at validation-selected `tau=0.901`.
- Do not tune thresholds on the test set.
- Treat full-label tau sweeps as calibration diagnostics, not as replacements for the fixed-protocol tables.

## Live Mempool Benchmark

For a fresh live run, build a recent active protected-account file:

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

Run the six-hour collector:

```powershell
$env:APP_PROTECTED_ACCOUNTS_PATH="results\live_active_protected_accounts_24h_1000victims.json"

go run ./cmd/server --config configs\app.yaml `
  --live-benchmark-duration 6h `
  --live-benchmark-out results\live_mempool_YYYYMMDD_HHMM
```

The committed VPS run used `drpc_pendingTransactions`, a 60-second or 5-block warmup exclusion, sequential block accounting, and Telegram Bot API `sendMessage` acceptance timing. Visibility loss is provider-specific public-feed visibility: `1 - included_seen_pending_rate`, not global mempool truth.

## Result Artifacts

The committed artifact bundle contains the exact JSON/CSV/JSONL files used for the current paper tables and live supplement:

```text
results/paper_artifacts_20260619/
  paper_results_summary.json
  artifact_inventory.csv
  source_hashes.json
  tables/
    rq1_detection_quality.csv
    rq2_full_replay_lookup.csv
    rq2_controlled_10000_counterparties.csv
    rq3_lr_feature_ablation.csv
    rq4_pending_visibility_loss_simulation.csv
    live_mempool_microbenchmark.csv
  live_mempool_20260619T0316_1000v/
    live_mempool_metrics.json
    live_mempool_blocks.csv
    live_mempool_alerts.jsonl
    run_manifest.json
  protected_accounts/
    live_active_protected_accounts_24h_1000victims.json
    live_active_protected_accounts_24h_1000victims.json.manifest.json
    selected_victims_summary.csv
  rq3_lr_feature_ablation/
    summary_aggregate.json
```

The full live `live_mempool_events.csv` from the six-hour run is not committed because it is about 244 MB. Raw SQL, Parquet caches, RPC caches, binaries, and local manuscript drafts are also ignored.

## Reference Numbers

- RQ1 learned LR row: precision `0.999923`, recall `0.957455`, F1 `0.978229`.
- RQ2 full replay lookup: `mempool_trieguard` mean `0.003565` ms vs `linear_scan` mean `0.143894` ms.
- RQ3 calibrated LR ablation: full address+type+token F1 mean `0.979535`; address-only F1 mean `0.979512`.
- RQ4 simulated pending loss: recall falls from `0.957455` at `0%` loss to `0.478557` at `50%` loss.
- Live run: `813,092` pending messages over six hours, detector p99 `0.016519` ms, lookup p99 `0.023420` ms, Telegram acceptance p99 `956.208` ms, visibility loss `43.003%` for all included transactions and `24.210%` for direct ERC-20 transfer calls.

## Artifact Policy

Commit code, config templates, root documentation, and curated paper artifacts. Do not commit `.env`, real provider URLs, API keys, raw datasets, `dataset/`, `dataset.zip`, `data/`, Parquet files, full generated `results/`, local binaries, or local manuscript drafts under `paper/`.
