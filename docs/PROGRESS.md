# Progress Handoff

## Table of Contents

- [Related Docs](#related-docs)
- [Current Status](#current-status)
- [Latest Full-Dataset Result](#latest-full-dataset-result)
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
- Manuscript files are local-only and excluded from the public code repo.
- `results/` contains the run outputs locally but is ignored by Git.

## Latest Full-Dataset Result

Using fixed `tau=0.40` and replay delays `5/15/30` seconds:

| Method | Precision | Recall | F1 |
|---|---:|---:|---:|
| Mempool-TrieGuard | 0.999214 | 0.957280 | 0.977797 |
| Linear scan | 0.978671 | 0.965444 | 0.972012 |
| Confirmed-chain | 1.000000 | 0.283724 | 0.442033 |

RQ4 recall for Mempool-TrieGuard:

| Mempool loss | Recall |
|---:|---:|
| 0% | 0.957280 |
| 10% | 0.861548 |
| 25% | 0.717912 |
| 50% | 0.478467 |

## What To Improve Next

- Tune the risk-score weights because `address_only_trie` slightly beats current production F1 on the full-label run.
- Add better token/counterfeit features so token context improves F1 instead of mainly providing explainability.
- Add memory and update-time metrics because the experiment guide asks for operational overhead.
- Add a real 10/100/1000/10000 counterparty-size lookup scaling table if the report needs strict RQ2 scaling instead of full-label aggregate lookup metrics.
- Run live VPS validation with rotated dRPC key and Basic Auth.

## Do Not Commit

- `.env` or real API keys.
- `results/`.
- `29212703/`, `29212703.zip`, Parquet datasets, or dRPC caches.
- `detector-cli.exe`, `server.exe`, or other local binaries.




