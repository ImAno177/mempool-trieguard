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

- Go detector, live dRPC monitor, Web UI, and Python benchmark pipeline are implemented.
- Full-label benchmark mode is implemented with victim sharding and resume-safe summary files.
- Full dataset run completed locally with `34,905,969` rows.
- Full-label tau sweep mode is implemented and completed for the current dataset at loss rate `0`.
- A local LaTeX paper draft was written at `paper/mempool_trieguard_full_dataset_paper_20260523.tex`.
- Manuscript files are local-only and excluded from the public code repo.
- `results/` contains the run outputs locally but is ignored by Git.

## Latest Full-Dataset Result

Using fixed `tau=0.40` and replay delays `5/15/30` seconds:

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Mempool-TrieGuard | 0.999214 | 0.957280 | 0.977797 |
| Linear scan | 0.978671 | 0.965444 | 0.972012 |
| Confirmed-chain | 1.000000 | 0.283724 | 0.442033 |

RQ3 ablation at the same fixed threshold:

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Address-only trie | 0.999811 | 0.957291 | 0.978089 |
| Mempool-TrieGuard | 0.999214 | 0.957280 | 0.977797 |
| No time | 0.999547 | 0.957480 | 0.978062 |
| No token | 0.998967 | 0.948913 | 0.973297 |
| No value | 0.999129 | 0.957496 | 0.977869 |
| Prefix only | 0.999564 | 0.957186 | 0.977916 |
| Suffix only | 0.999621 | 0.957189 | 0.977945 |

RQ4 recall for Mempool-TrieGuard:

| Mempool loss | Recall |
|---:|---:|
| 0% | 0.957280 |
| 10% | 0.861548 |
| 25% | 0.717912 |
| 50% | 0.478467 |

Interpretation: trie retrieval is strong and fast, but the current contextual risk score is not yet calibrated well enough to beat every ablation. In particular, `address_only_trie` and `no_time` are slightly better by F1 at `tau=0.40`, so the paper should present this as a calibration finding.

## Latest Tau Sweep

Completed sweep directory: `results/full_label_tau_sweep_20260523` locally. The directory is ignored by Git.

Scope: loss rate `0`, all full-label shards, replay delays `5/15/30` seconds, baseline threshold `tau=0.40`.

| Method | Best tau | Best F1 | F1 at tau=0.40 | Delta F1 |
|---|---:|---:|---:|---:|
| `address_only_trie` | 0.505 | 0.978172 | 0.978089 | +0.000083 |
| `mempool_trieguard` | 0.395 | 0.977826 | 0.977797 | +0.000029 |
| `no_time` | 0.430 | 0.978073 | 0.978062 | +0.000011 |
| `no_token` | 0.335 | 0.976106 | 0.973297 | +0.002809 |
| `no_value` | 0.430 | 0.977976 | 0.977869 | +0.000107 |
| `prefix_only` | 0.390 | 0.977967 | 0.977916 | +0.000050 |
| `suffix_only` | 0.390 | 0.978000 | 0.977945 | +0.000054 |

The sweep shows that `tau=0.40` is nearly optimal for `mempool_trieguard`; changing to `0.395` improves F1 by only `0.000029`.

## Latest Missing Experiments

Completed directory: `results/missing_experiments_20260523` locally. The directory is ignored by Git.

Scope: controlled per-wallet benchmark on shard `0036`, victim `0x79672062c5a45e3808d6b784129cf3ecf59d4224`, with `63,607` available trusted counterparties. Each method/size was run `30` times on a `10,000`-event replay sample with delay `15` seconds and loss rate `0`.

RQ2 strict per-wallet scaling:

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

Operational overhead:

| Counterparties | Load/update mean ms | Heap per wallet KB | Heap per 1k counterparties KB |
|---:|---:|---:|---:|
| 10 | 0.021030 | 14.53 | 1,453.44 |
| 100 | 0.055673 | 32.26 | 322.64 |
| 1,000 | 0.622817 | 163.73 | 163.73 |
| 10,000 | 6.491340 | 1,608.84 | 160.88 |

## Paper Draft

The current local manuscript draft is `paper/mempool_trieguard_full_dataset_paper_20260523.tex`. It includes:

- Full-label dataset scope and fixed-threshold RQ1-RQ4 results.
- Exploratory tau sweep results.
- A discussion explaining why ablations can slightly outperform the full method.
- A proposed next risk-score direction that keeps address similarity as the core signal and uses contextual features as bounded modifiers.
- The newer per-wallet RQ2 scaling and overhead artifacts are ready in `results/missing_experiments_20260523` for manuscript integration.

## What To Improve Next

- Calibrate the risk score on held-out accounts/time ranges rather than tuning on the same full-label aggregate.
- Test a bounded-modifier score where address similarity remains mandatory and token/type/time/value act as small boosts or penalties.
- Add better token/counterfeit features so token context improves F1 instead of mainly providing explainability.
- Integrate the new per-wallet scaling and operational-overhead tables into the manuscript if they are needed for the next paper revision.
- Run live VPS validation with rotated dRPC key and Basic Auth.

## Do Not Commit

- `.env` or real API keys.
- `results/`.
- `paper/` local manuscript drafts unless repository policy changes.
- `29212703/`, `29212703.zip`, Parquet datasets, or dRPC caches.
- `detector-cli.exe`, `server.exe`, or other local binaries.




