#!/usr/bin/env python3
"""Rerun the controlled per-wallet RQ2 scaling and load-profile experiment."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import os
import shutil
import statistics
import subprocess
from pathlib import Path


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as fobj:
        writer = csv.DictWriter(fobj, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fobj:
        return list(csv.DictReader(fobj))


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    idx = q * (len(xs) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def run_detector(args: argparse.Namespace, cp_path: Path, replay_path: Path, method: str, out_dir: Path) -> dict:
    summary_path = out_dir / f"summary_{method}.json"
    cmd = [
        args.detector_cli,
        "--config",
        str(args.config),
        "--counterparties",
        str(cp_path),
        "--replay",
        str(replay_path),
        "--method",
        method,
        "--out",
        str(out_dir),
        "--token-metadata",
        str(args.token_metadata),
        "--no-alerts",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"detector-cli failed: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def write_profile_load_go(path: Path) -> None:
    path.write_text(
        r'''package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"runtime"
	"time"

	"mempool-trieguard/internal/bench"
	"mempool-trieguard/internal/config"
	"mempool-trieguard/internal/detector"
)

func main() {
	cfgPath := flag.String("config", "configs/app.yaml", "app config path")
	counterpartiesPath := flag.String("counterparties", "", "counterparties json path")
	outPath := flag.String("out", "", "output csv path")
	runs := flag.Int("runs", 30, "number of runs")
	flag.Parse()
	if *counterpartiesPath == "" || *outPath == "" {
		panic("counterparties and out are required")
	}
	cfg, err := config.Load(*cfgPath)
	if err != nil {
		panic(err)
	}
	cps, err := bench.LoadCounterpartiesJSON(*counterpartiesPath)
	if err != nil {
		panic(err)
	}
	dcfg := detector.Config{
		WindowDays: cfg.Detector.WindowDays, KP: cfg.Detector.KP, KS: cfg.Detector.KS,
		ThetaP: cfg.Detector.ThetaP, ThetaS: cfg.Detector.ThetaS,
		MinPrefixDepth: cfg.Detector.MinPrefixDepth, MinSuffixDepth: cfg.Detector.MinSuffixDepth,
		MaxCandidatesPerSide: cfg.Detector.MaxCandidatesPerSide, Tau: cfg.Detector.Tau,
		Lambda: cfg.Detector.Lambda, ScoreMode: cfg.Detector.ScoreMode,
		LogisticIntercept: cfg.Detector.LogisticIntercept,
		AddressScoreMode: cfg.Detector.AddressScoreMode,
		AddressBalanceAlpha: cfg.Detector.AddressBalanceAlpha,
		AddressBalanceGamma: cfg.Detector.AddressBalanceGamma,
		ContextGateBase: cfg.Detector.ContextGateBase, TinyValue: cfg.Detector.TinyValue,
	}
	if len(cfg.Detector.Weights) == 5 {
		copy(dcfg.Weights[:], cfg.Detector.Weights)
	}
	if len(cfg.Detector.ContextWeights) == 4 {
		copy(dcfg.ContextWeights[:], cfg.Detector.ContextWeights)
	}
	if len(cfg.Detector.LogisticWeights) == 3 {
		copy(dcfg.LogisticWeights[:], cfg.Detector.LogisticWeights)
	}
	out, err := os.Create(*outPath)
	if err != nil {
		panic(err)
	}
	defer out.Close()
	w := csv.NewWriter(out)
	defer w.Flush()
	_ = w.Write([]string{"run_id","counterparty_size","protected_victims","load_counterparties_ms","heap_delta_kb","heap_per_wallet_kb","heap_per_1k_counterparties_kb"})
	for runID := 0; runID < *runs; runID++ {
		runtime.GC()
		var before runtime.MemStats
		runtime.ReadMemStats(&before)
		start := time.Now()
		eng := detector.NewEngine(dcfg)
		if err := eng.LoadCounterparties(cps); err != nil {
			panic(err)
		}
		loadMs := float64(time.Since(start).Nanoseconds()) / 1e6
		runtime.GC()
		var after runtime.MemStats
		runtime.KeepAlive(eng)
		runtime.ReadMemStats(&after)
		victims := eng.ProtectedVictimCount()
		if victims <= 0 {
			victims = 1
		}
		deltaBytes := int64(after.HeapAlloc) - int64(before.HeapAlloc)
		if deltaBytes < 0 {
			deltaBytes = 0
		}
		heapKB := float64(deltaBytes) / 1024.0
		perWalletKB := heapKB / float64(victims)
		per1kKB := 0.0
		if len(cps) > 0 {
			per1kKB = heapKB * 1000.0 / float64(len(cps))
		}
		_ = w.Write([]string{
			fmt.Sprintf("%d", runID), fmt.Sprintf("%d", len(cps)), fmt.Sprintf("%d", victims),
			fmt.Sprintf("%.6f", loadMs), fmt.Sprintf("%.6f", heapKB), fmt.Sprintf("%.6f", perWalletKB),
			fmt.Sprintf("%.6f", per1kKB),
		})
		eng = nil
		runtime.GC()
	}
}
''',
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun per-wallet RQ2 scaling experiment.")
    parser.add_argument("--source-dir", default="results/missing_experiments_20260523")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", default="configs/app.yaml")
    parser.add_argument("--detector-cli", default=".\\detector-cli.exe")
    parser.add_argument("--token-metadata", default="results/rpc_cache/full_dataset_token_metadata_cache.json")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--sizes", default="10,100,1000,10000")
    parser.add_argument("--methods", default="mempool_trieguard,linear_scan,db_index,dblsh2_display")
    parser.add_argument("--jobs", type=int, default=max(1, min(6, os.cpu_count() or 1)))
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    out_dir = Path(args.out_dir)
    assets = source_dir / "assets"
    replay_path = assets / "replay_shard_0036_victim_f59d4224_10000_delay15.jsonl"
    sizes = [int(x.strip()) for x in args.sizes.split(",") if x.strip()]
    methods = [method.strip() for method in args.methods.split(",") if method.strip()]
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(assets, out_dir / "assets", dirs_exist_ok=True)

    detector_jobs: list[tuple[int, str, int, Path, Path]] = []
    for size in sizes:
        cp_path = assets / f"counterparties_victim_f59d4224_{size}.json"
        for method in methods:
            for run_id in range(args.runs):
                run_out = out_dir / "scaling_runs" / f"size_{size:05d}" / method / f"run_{run_id:02d}"
                detector_jobs.append((size, method, run_id, cp_path, run_out))

    raw_rows: list[dict[str, object]] = []
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(args.jobs))) as executor:
        futures = {
            executor.submit(run_detector, args, cp_path, replay_path, method, run_out): (size, method, run_id)
            for size, method, run_id, cp_path, run_out in detector_jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            size, method, run_id = futures[fut]
            payload = fut.result()
            metrics = payload["metrics"]
            raw_rows.append(
                {
                    "method": method,
                    "counterparty_size": size,
                    "run_id": run_id,
                    "replay_events": metrics.get("total_events", 0),
                    "lookup_mean_ms": metrics.get("lookup_mean_ms", 0),
                    "lookup_p95_ms": metrics.get("lookup_p95_ms", 0),
                    "lookup_p99_ms": metrics.get("lookup_p99_ms", 0),
                    "throughput_tps": metrics.get("throughput_tps", 0),
                    "average_candidates_scored": metrics.get("average_candidates_scored", 0),
                    "f1": metrics.get("f1", 0),
                }
            )
            completed += 1
            print(f"scaling [{completed}/{len(detector_jobs)}] size={size} method={method} run={run_id:02d}", flush=True)

    raw_rows.sort(key=lambda row: (int(row["counterparty_size"]), str(row["method"]), int(row["run_id"])))
    write_csv(out_dir / "rq2_per_wallet_scaling_raw.csv", raw_rows)

    summary_rows: list[dict[str, object]] = []
    for size in sizes:
        for method in methods:
            group = [row for row in raw_rows if row["counterparty_size"] == size and row["method"] == method]
            summary_rows.append(
                {
                    "method": method,
                    "counterparty_size": size,
                    "runs": len(group),
                    "replay_events": group[0]["replay_events"] if group else 0,
                    "lookup_mean_ms_mean": mean([float(row["lookup_mean_ms"]) for row in group]),
                    "lookup_mean_ms_std": stdev([float(row["lookup_mean_ms"]) for row in group]),
                    "lookup_p95_ms_mean": mean([float(row["lookup_p95_ms"]) for row in group]),
                    "lookup_p99_ms_mean": mean([float(row["lookup_p99_ms"]) for row in group]),
                    "throughput_tps_mean": mean([float(row["throughput_tps"]) for row in group]),
                    "throughput_tps_std": stdev([float(row["throughput_tps"]) for row in group]),
                    "average_candidates_scored_mean": mean([float(row["average_candidates_scored"]) for row in group]),
                    "f1_mean": mean([float(row["f1"]) for row in group]),
                }
            )
    write_csv(out_dir / "rq2_per_wallet_scaling_summary.csv", summary_rows)

    profile_go = out_dir / "profile_load.go"
    write_profile_load_go(profile_go)
    overhead_raw: list[dict[str, object]] = []
    for size in sizes:
        cp_path = assets / f"counterparties_victim_f59d4224_{size}.json"
        profile_out = out_dir / f"load_profile_size_{size}.csv"
        cmd = ["go", "run", str(profile_go), "--config", str(args.config), "--counterparties", str(cp_path), "--out", str(profile_out), "--runs", str(args.runs)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"go load profile failed: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        for row in read_csv(profile_out):
            row["size_label"] = size
            overhead_raw.append(row)
        print(f"load profile size={size}", flush=True)
    write_csv(out_dir / "operational_overhead_raw.csv", overhead_raw)

    overhead_summary: list[dict[str, object]] = []
    for size in sizes:
        group = [row for row in overhead_raw if int(row["counterparty_size"]) == size]
        overhead_summary.append(
            {
                "counterparty_size": size,
                "runs": len(group),
                "protected_victims": int(group[0]["protected_victims"]) if group else 0,
                "load_counterparties_mean_ms": mean([float(row["load_counterparties_ms"]) for row in group]),
                "load_counterparties_std_ms": stdev([float(row["load_counterparties_ms"]) for row in group]),
                "load_counterparties_p95_ms": percentile([float(row["load_counterparties_ms"]) for row in group], 0.95),
                "heap_delta_mean_kb": mean([float(row["heap_delta_kb"]) for row in group]),
                "heap_delta_std_kb": stdev([float(row["heap_delta_kb"]) for row in group]),
                "heap_per_wallet_mean_kb": mean([float(row["heap_per_wallet_kb"]) for row in group]),
                "heap_per_1k_counterparties_mean_kb": mean([float(row["heap_per_1k_counterparties_kb"]) for row in group]),
            }
        )
    write_csv(out_dir / "operational_overhead_summary.csv", overhead_summary)

    metadata = json.loads((source_dir / "experiment_metadata.json").read_text(encoding="utf-8"))
    metadata.update(
        {
            "rerun_created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "rerun_source_dir": str(source_dir),
            "rerun_out_dir": str(out_dir),
            "benchmark_runs_per_config": args.runs,
        }
    )
    (out_dir / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"wrote RQ2 rerun to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
