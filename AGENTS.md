# AGENTS.md

## Project Overview

Mempool-TrieGuard is a research prototype for pre-confirmation Ethereum address-poisoning detection. The main runtime is Go; the dataset and replay benchmark pipeline are Python. The detector builds time-aware prefix/suffix tries over trusted counterparties, screens pending ERC-20 transfer views, and scores lookalike candidates with a validation-calibrated lightweight LR score.

## Setup Commands

```powershell
go mod download
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```

Linux shortcuts:

```bash
make verify
make build-linux
```

Run the local JSON control server:

```powershell
go run ./cmd/server --config configs\app.yaml
```

## Code Style

- Prefer Go for detector, live runtime, RPC, and server behavior.
- Prefer Python for dataset normalization, replay orchestration, statistics, and table generation.
- Keep CLI flags backward compatible; `python/benchmark_pipeline.py` calls `cmd/detector-cli` directly.
- Do not hardcode RPC endpoints, API keys, Telegram tokens, or passwords.
- Preserve output schemas for benchmark summaries unless the paper protocol is explicitly updated.
- Keep comments short and useful; avoid restating the code.

## Testing Instructions

Run these before publishing or pushing behavior changes:

```powershell
python -m py_compile python/benchmark_pipeline.py
go test ./...
go build -o detector-cli.exe ./cmd/detector-cli
go build -o server.exe ./cmd/server
```

For paper-only LaTeX edits, also run from `paper/` when the manuscript is present locally:

```powershell
pdflatex -interaction=nonstopmode -halt-on-error paper.tex
```

## Detector Invariants

- Production method name is `mempool_trieguard`.
- Decision policy is score-first: emit an alert when `score.Total >= tau`.
- Trie retrieval is candidate generation; do not add hard prefix/suffix gates that drop positives before scoring.
- Trusted counterparties are time-aware: a record is valid only when `last_seen <= observed_at` and inside the configured history window.
- Exact trusted-counterparty matches must not become poisoning alerts.
- Token metadata can be missing; scoring must degrade gracefully.

## Benchmark Rules

- Legacy additive baselines keep their documented fixed thresholds.
- The current learned LR `mempool_trieguard` row uses validation-selected `tau=0.901`.
- Do not tune thresholds on the test set.
- Treat full-label tau sweep results as exploratory calibration diagnostics, not replacements for fixed-protocol RQ tables.
- RQ3 ablations use the same LR-family training protocol with restricted feature sets and validation-selected thresholds.
- RQ4 simulated pending-visibility loss applies to `mempool_trieguard`.
- Live microbenchmark results do not provide live precision or recall because the provider feed has no poisoning ground truth.
- Live visibility loss is a provider-specific public-feed proxy, computed as `1 - seen_pending_rate` over post-warmup included transactions.

## Current Paper Metrics

- Dataset scope: 34,905,969 rows, 17,365,954 positives, 17,516,047 negatives, 256 shards.
- Main replay row: precision 0.999923, recall 0.957455, F1 0.978229 at `tau=0.901`.
- Full-replay lookup: `mempool_trieguard` mean 0.003565 ms vs `linear_scan` 0.143894 ms.
- Controlled 10,000-counterparty lookup: trie 0.001443 ms, linear scan 0.211963 ms, DB index 0.342162 ms, DB-LSH-style 0.247552 ms.
- RQ3 calibrated LR feature ablation: deployed MTG feature-sample F1 0.979324, address-only LR ablation F1 mean 0.979264, delta -5.99e-5 relative to the deployed feature-sample row.
- Live run: 813,092 pending messages over six hours, detector p99 0.016519 ms, lookup p99 0.023420 ms, pending inter-arrival p50/p99 1.940/339.684 ms, message-channel acceptance p99 956.208 ms.
- Live visibility proxy: 43.003% for all included transactions and 24.210% for included direct ERC-20 transfer calls.

## Artifact Policy

Commit only code, config templates, public docs, and the curated paper artifact bundle:

```text
results/paper_artifacts_20260619/
```

Do not commit:

- `.env`, `.env.*` except `.env.example`, API keys, dRPC URLs with embedded keys, Telegram bot tokens, or passwords.
- Raw datasets, `dataset/`, `dataset.zip`, `data/`, Parquet files, local RPC caches, full live event CSVs, or full generated `results/` trees.
- Binaries such as `detector-cli.exe`, `server.exe`, shared libraries, or release artifacts.
- Local manuscript drafts under `paper/` unless the user explicitly changes the repository policy.

## PR And Commit Notes

- Keep changes narrowly tied to the requested experiment, paper, or runtime behavior.
- State whether replay results, live results, or both are affected.
- Mention any benchmark artifacts used to update paper numbers.
- Re-run relevant checks and report failures plainly.
