# Internal Go Agent Guide

## Table of Contents

- [Scope](#scope)
- [Documentation Links](#documentation-links)
- [Detector Invariants](#detector-invariants)
- [Baseline Rules](#baseline-rules)

## Scope

This folder contains core Go packages: detector, benchmark metrics, live dRPC ingestion, config, store, and Web handlers.

## Documentation Links

- [Root README](../README.md) - high-level workflow.
- [Experiment guide](../docs/EXPERIMENT_GUIDE.md) - required baselines and metrics.
- [Progress handoff](../docs/PROGRESS.md) - current status and open improvements.
- [Python benchmark guide](../python/AGENTS.md) - how the detector is called from full-label replay.

## Detector Invariants

- Production method is `mempool_trieguard`.
- Decision policy is risk-score-first: emit an alert when `score.Total >= tau`.
- Trie retrieval is for candidate generation; avoid hard `theta_p/theta_s` gates that drop positives before scoring.
- Counterparty history is time-aware and must not use future labels for a pending event.
- Keep exact address matches out of poisoning alerts.
- Token metadata can be missing; scoring must degrade gracefully.

## Baseline Rules

- `confirmed_chain`: post-confirmation style detector for RQ1.
- `linear_scan`: compare each pending transfer with the full `R_v` for RQ2.
- `address_only_trie`, `prefix_only`, `suffix_only`, `no_token`, `no_time`, `no_value`: RQ3 ablations.
- Loss rates are only for `mempool_trieguard` in RQ4.



