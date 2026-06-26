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
| `results/paper_artifacts_20260619/` | Paper tables, manifests, and supporting result artifacts. |

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

Set runtime secrets only through `.env` or the shell.

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
  --tau-grid 0.901 `
  --benchmark-runs 1 `
  --jobs 12
```

Protocol rules:

- Legacy additive baselines keep their fixed thresholds.
- The current `mempool_trieguard` paper row uses the learned LR score at validation-selected `tau=0.901`.
- Do not tune thresholds on the test set.
- Treat full-label tau sweeps as calibration diagnostics, not as replacements for the fixed-protocol tables.

## Reproducing Paper Experiments

The paper tables are produced from the normalized Parquet cache and the detector CLI. The commands below use timestamped output directories so repeated runs do not overwrite prior artifacts.

RQ1 and RQ4 use the full-label replay. RQ1 reads `full_label_rq1.csv`; RQ4 reads `full_label_rq4_loss_robustness.csv`.

```powershell
go build -o detector-cli.exe ./cmd/detector-cli

python -u python/benchmark_pipeline.py `
  --full-label-replay `
  --dataset-cache data/normalized/address_poisoning_ethereum.normalized.full.parquet `
  --max-rows 0 `
  --shard-count 256 `
  --shard-batch-size 4 `
  --results-dir results/reproduce_rq1_rq4_YYYYMMDD `
  --detector-cli .\detector-cli.exe `
  --token-cache results/rpc_cache/full_dataset_token_metadata_cache.json `
  --no-rpc-enrich `
  --loss-rates 0,0.10,0.25,0.50 `
  --tau-grid 0.901 `
  --benchmark-runs 1 `
  --jobs 12
```

RQ2 full-replay lookup cost is written by the same run to `full_label_rq2_lookup_scaling.csv`. The controlled 10,000-counterparty scaling table is rerun from the fixed high-activity replay asset:

```powershell
python scripts\rerun_rq2_scaling.py `
  --source-dir results\missing_experiments_20260523 `
  --out-dir results\reproduce_rq2_scaling_YYYYMMDD `
  --config configs\app.yaml `
  --detector-cli .\detector-cli.exe `
  --token-metadata results\rpc_cache\full_dataset_token_metadata_cache.json `
  --runs 30 `
  --sizes 10,100,1000,10000 `
  --methods mempool_trieguard,linear_scan,db_index,dblsh2_display `
  --jobs 6
```

RQ3 uses LR models trained separately for each ablation. First export the full feature sample from the full-label shards, then train 30 split-victim LR ablation runs and summarize them:

```powershell
python scripts\export_risk_training_dataset.py `
  --shards-dir results\reproduce_rq1_rq4_YYYYMMDD\full_label_shards `
  --source-manifest results\reproduce_rq1_rq4_YYYYMMDD\full_label_manifest.json `
  --dataset-cache data\normalized\address_poisoning_ethereum.normalized.full.parquet `
  --token-metadata results\rpc_cache\full_dataset_token_metadata_cache.json `
  --out-dir results\reproduce_rq3_features_YYYYMMDD `
  --jobs 12 `
  --skip-block-number-enrich

for ($i = 0; $i -lt 30; $i++) {
  $run = "{0:D2}" -f $i
  python scripts\learn_lr_feature_ablation.py `
    --feature-dir results\reproduce_rq3_features_YYYYMMDD\features `
    --out-dir results\reproduce_rq3_lr_ablation_YYYYMMDD\run_$run `
    --split-column split_victim `
    --seed $i `
    --epochs 1 `
    --threshold-grid-size 1000
}

python scripts\summarize_lr_ablation_runs.py `
  --runs-dir results\reproduce_rq3_lr_ablation_YYYYMMDD `
  --out-dir results\reproduce_rq3_lr_ablation_YYYYMMDD
```

Daily confidence intervals and paired lookup tests are computed from the full-label replay's daily metrics:

```powershell
python scripts\full_label_daily_stats.py `
  --daily-metrics results\reproduce_rq1_rq4_YYYYMMDD\full_label_daily_metrics_by_day.csv `
  --out-dir results\reproduce_daily_stats_YYYYMMDD `
  --bootstrap-samples 10000 `
  --seed 1337
```

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

The six-hour VPS run used `drpc_pendingTransactions`, a 60-second or 5-block warmup exclusion, sequential block accounting, and message-channel acceptance timing through the Telegram Bot API. Visibility loss is provider-specific public-feed visibility: `1 - included_seen_pending_rate`, not global mempool truth.

## Result Artifacts

The artifact bundle contains the JSON/CSV/JSONL files used for the paper tables and live supplement:

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
  graphica/
    live_mempool_alerts_tidy.csv
    live_mempool_microbenchmark_tidy.csv
    mempool_trieguard_evidence_graph.csv
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
    summary_by_run.csv
```

The full live `live_mempool_events.csv` from the six-hour run is about 244 MB and is intended for the final archival replication package, such as Zenodo. Raw SQL, Parquet caches, RPC caches, binaries, and local manuscript drafts are not part of this repository.
