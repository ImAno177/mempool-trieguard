# Progress Handoff

## Table of Contents

- [Related Docs](#related-docs)
- [Current Status](#current-status)
- [Latest Full-Dataset Result](#latest-full-dataset-result)
- [Latest Tau Sweep](#latest-tau-sweep)
- [Latest Missing Experiments](#latest-missing-experiments)
- [Paper Draft](#paper-draft)
- [What To Improve Next](#what-to-improve-next)
- [Do Not Commit](#do-not-commit)

## Related Docs

- [README](../README.md) - commands and artifact policy.
- [Setup guide](SETUP.md) - environment setup and runbook.
- [Experiment guide](EXPERIMENT_GUIDE.md) - RQ1-RQ4, baselines, metrics.
- [Dataset notes](DATASET.md) - local Parquet/cache files.
- [Root AGENTS](../AGENTS.md) - AI/code-agent rules.

## Current Status

- Go detector, live dRPC monitor, headless live mempool micro-benchmark, Web UI, and Python benchmark pipeline are implemented.
- Full-label benchmark mode is implemented with victim sharding and resume-safe summary files.
- Full dataset run completed locally with `34,905,969` rows.
- Full-label tau sweep mode is implemented and completed for the legacy additive score at loss rate `0`.
- The main detector row now uses the learned LR score with address+type+token features and validation-selected `tau=0.901`.
- The current local IEEEtran paper draft is `paper/paper.tex`.
- Manuscript files are local-only and excluded from the public code repo.
- `results/` contains the run outputs locally but is ignored by Git.

## Latest Full-Dataset Result

Using replay delays `5/15/30` seconds. Legacy baselines keep their fixed thresholds; the main Mempool-TrieGuard row uses learned LR score at `tau=0.901`:

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Mempool-TrieGuard LR full | 0.999923 | 0.957455 | 0.978229 |
| Linear scan | 0.978671 | 0.965444 | 0.972012 |
| Confirmed-chain | 1.000000 | 0.283724 | 0.442033 |

RQ3 calibrated LR feature ablation, split-victim, 30 runs:

| Variant | Runs | F1 mean | Precision mean | Recall mean | Delta F1 mean |
|---|---:|---:|---:|---:|---:|
| Full address+type+token | 30 | 0.979535 | 0.999825 | 0.960052 | 0.000000 |
| No token | 30 | 0.979513 | 0.999991 | 0.959857 | -0.000022 |
| Address only | 30 | 0.979512 | 0.999991 | 0.959855 | -0.000023 |
| No type | 30 | 0.979511 | 0.999989 | 0.959855 | -0.000024 |
| Suffix only | 30 | 0.979511 | 0.999858 | 0.959974 | -0.000025 |
| Prefix only | 30 | 0.978909 | 0.998613 | 0.959966 | -0.000627 |

RQ4 recall for Mempool-TrieGuard:

| Mempool loss | Recall |
|---:|---:|
| 0% | 0.957455 |
| 10% | 0.861707 |
| 25% | 0.718047 |
| 50% | 0.478557 |

Interpretation: trie retrieval remains the speed advantage, and the learned LR score is now the manuscript's main scoring formula. Full address+type+token is the best calibrated LR ablation, but its margin over address-only/no-type/no-token is small and should be reported conservatively.

## Latest Tau Sweep

Completed sweep directory: `results/full_label_tau_sweep_20260523` locally. The directory is ignored by Git.

Scope: loss rate `0`, all full-label shards, replay delays `5/15/30` seconds, baseline threshold `tau=0.40`. This sweep is diagnostic for the old additive score, not the current LR main detector.

| Method | Best tau | Best F1 | F1 at tau=0.40 | Delta F1 |
|---|---:|---:|---:|---:|
| `address_only_trie` | 0.505 | 0.978172 | 0.978089 | +0.000083 |
| `mempool_trieguard` | 0.395 | 0.977826 | 0.977797 | +0.000029 |
| `no_time` | 0.430 | 0.978073 | 0.978062 | +0.000011 |
| `no_token` | 0.335 | 0.976106 | 0.973297 | +0.002809 |
| `no_value` | 0.430 | 0.977976 | 0.977869 | +0.000107 |
| `prefix_only` | 0.390 | 0.977967 | 0.977916 | +0.000050 |
| `suffix_only` | 0.390 | 0.978000 | 0.977945 | +0.000054 |

The sweep shows that `tau=0.40` was nearly optimal for the old additive `mempool_trieguard` score; changing to `0.395` improved F1 by only `0.000029`. The current main detector instead uses a validation-selected LR threshold.

## Latest Missing Experiments

Completed directory: `results/missing_experiments_20260523` locally. Additional DB-index and DB-LSH-style baseline rows are in `results/rq2_two_baselines_30run_20260615`. These directories are ignored by Git.

Scope: controlled per-wallet benchmark on shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, with `63,607` available trusted counterparties. Each method/size was run `30` times on a `10,000`-event replay sample with delay `15` seconds and loss rate `0`.

RQ2 strict per-wallet scaling:

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

Operational overhead:

| Counterparties | Load/update mean ms | Heap per wallet KB | Heap per 1k counterparties KB |
|---:|---:|---:|---:|
| 10 | 0.021030 | 14.53 | 1,453.44 |
| 100 | 0.055673 | 32.26 | 322.64 |
| 1,000 | 0.622817 | 163.73 | 163.73 |
| 10,000 | 6.491340 | 1,608.84 | 160.88 |

## Paper Draft

The current local manuscript draft is `paper/paper.tex`. It includes:

- Full-label dataset scope and fixed-threshold RQ1-RQ4 results.
- Aggregate RQ2 lookup mean/p95/p99, throughput, and candidate counts.
- Per-wallet RQ2 scaling and operational-overhead tables from `results/missing_experiments_20260523`, plus DB-index and DB-LSH-style baseline rows from `results/rq2_two_baselines_30run_20260615`.
- Exploratory tau sweep results.
- A discussion explaining why ablations can slightly outperform the full method.
- A proposed next risk-score direction that keeps address similarity as the core signal and uses contextual features as bounded modifiers.
- Replay wording based on `observed_at = block_time - delay`, with the live-mempool limitation stated explicitly.
- A `Code and Data Availability` section pointing to the public GitHub repository while keeping raw datasets, caches, and generated results out of version control.

## What To Improve Next

- Calibrate the risk score on held-out accounts/time ranges rather than tuning on the same full-label aggregate.
- Test a bounded-modifier score where address similarity remains mandatory and token/type/time/value act as small boosts or penalties.
- Add better token/counterfeit features so token context improves F1 instead of mainly providing explainability.
- Run live VPS micro-benchmark with rotated dRPC key, real protected-account artifact, and Basic Auth for any UI session.

## Do Not Commit

- `.env` or real API keys.
- `results/`.
- `paper/` local manuscript drafts unless repository policy changes.
- `29212703/`, `29212703.zip`, Parquet datasets, or dRPC caches.
- `detector-cli.exe`, `server.exe`, or other local binaries.




