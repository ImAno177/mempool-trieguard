# Internal Go Agent Guide

## Table of Contents

- [Scope](#scope)
- [Documentation Links](#documentation-links)
- [Detector Invariants](#detector-invariants)
- [Baseline Rules](#baseline-rules)
- [Current RQ2 Position](#current-rq2-position)
- [Current Scoring Finding](#current-scoring-finding)

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
- `address_only_trie`, `prefix_only`, `suffix_only`, `no_token`, `no_type`: current LR-family RQ3 ablations; `no_time` and `no_value` belong to the legacy additive diagnostic sweep.
- Loss rates are only for `mempool_trieguard` in RQ4.

## Current RQ2 Position

- Full-label aggregate lookup used in the current manuscript: learned LR `mempool_trieguard` mean `0.003891` ms, p95 `0.000000` ms, p99 `0.000000` ms vs legacy `linear_scan` mean `0.143894` ms, p95 `1.070443` ms, p99 `2.258818` ms.
- Strict per-wallet scaling artifact: `results/missing_experiments_20260523`.
- At 10,000 counterparties for the selected high-activity wallet, `mempool_trieguard` lookup mean is `0.000668` ms and `linear_scan` lookup mean is `0.211963` ms across 30 runs.
- Operational overhead at 10,000 counterparties is `6.491340` ms mean load/update time and `160.88` KB heap per 1,000 counterparties.

## Current Scoring Finding

- Current learned LR scoring result: full address+type+token is the best canonical 30-run ablation, with F1 mean `0.979535`.
- Legacy additive tau sweep finds `mempool_trieguard` best tau `0.395` with F1 `0.977826`, only `+0.000029` over `tau=0.40`; keep this as diagnostic history.
- Any risk-score improvement should be validated on held-out accounts or time ranges and should preserve the time-aware counterparty invariant.


