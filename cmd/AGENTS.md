# Command Agent Guide

## Table of Contents

- [Scope](#scope)
- [Documentation Links](#documentation-links)
- [Rules](#rules)

## Scope

This folder contains executable Go entrypoints.

- `cmd/detector-cli`: benchmark/replay CLI used by `python/benchmark_pipeline.py`.
- `cmd/server`: Web UI and live mempool monitor entrypoint.

## Documentation Links

- [Root README](../README.md) - setup and commands.
- [Root AGENTS](../AGENTS.md) - global repository rules.
- [Detector notes](../internal/AGENTS.md) - detector semantics used by both commands.

## Rules

- Keep CLI flags backward compatible when possible; Python pipeline calls `detector-cli` directly.
- `--no-alerts` is important for full-label runs because alert JSONL can be huge.
- Do not hardcode dRPC endpoints or API keys in Go code.
