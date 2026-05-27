#!/usr/bin/env python3
"""Compute daily-window statistical summaries for full-label results.

The input should be the aggregate daily metrics produced by
`benchmark_pipeline.py --day-boundaries ...`, usually
`full_label_daily_metrics_by_day.csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional speed path.
    np = None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fobj:
        return list(csv.DictReader(fobj))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as fobj:
        writer = csv.DictWriter(fobj, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def f(row: dict[str, object], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def i(row: dict[str, object], key: str, default: int = 0) -> int:
    return int(round(f(row, key, float(default))))


def safe_div(a: float, b: float) -> float:
    return 0.0 if b == 0 else a / b


def metric_from_counts(rows: Iterable[dict[str, object]], metric: str) -> float:
    tp = sum(i(row, "tp") for row in rows)
    fp = sum(i(row, "fp") for row in rows)
    fn = sum(i(row, "fn") for row in rows)
    tn = sum(i(row, "tn") for row in rows)
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = safe_div(2 * precision * recall, precision + recall)
    if metric == "precision":
        return precision
    if metric == "recall":
        return recall
    if metric == "f1":
        return f1
    if metric == "specificity":
        return safe_div(tn, tn + fp)
    raise ValueError(f"unsupported count metric: {metric}")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def bootstrap_count_ci(rows: list[dict[str, object]], metric: str, samples: int, seed: int) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    if np is not None:
        tp = np.array([i(row, "tp") for row in rows], dtype=np.float64)
        fp = np.array([i(row, "fp") for row in rows], dtype=np.float64)
        fn = np.array([i(row, "fn") for row in rows], dtype=np.float64)
        rng = np.random.default_rng(seed)
        values = []
        chunk_size = 1000
        for offset in range(0, samples, chunk_size):
            size = min(chunk_size, samples - offset)
            idx = rng.integers(0, len(rows), size=(size, len(rows)))
            tp_s = tp[idx].sum(axis=1)
            fp_s = fp[idx].sum(axis=1)
            fn_s = fn[idx].sum(axis=1)
            precision = np.divide(tp_s, tp_s + fp_s, out=np.zeros_like(tp_s), where=(tp_s + fp_s) != 0)
            recall = np.divide(tp_s, tp_s + fn_s, out=np.zeros_like(tp_s), where=(tp_s + fn_s) != 0)
            if metric == "precision":
                values.append(precision)
            elif metric == "recall":
                values.append(recall)
            elif metric == "f1":
                denom = precision + recall
                values.append(np.divide(2 * precision * recall, denom, out=np.zeros_like(denom), where=denom != 0))
            else:
                raise ValueError(f"unsupported count metric: {metric}")
        arr = np.concatenate(values)
        low, high = np.quantile(arr, [0.025, 0.975])
        return float(low), float(high)
    rng = random.Random(seed)
    n = len(rows)
    values = []
    for _ in range(samples):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        values.append(metric_from_counts(sample, metric))
    return percentile(values, 0.025), percentile(values, 0.975)


def ranks_with_average_ties(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    pos = 0
    while pos < len(indexed):
        end = pos + 1
        while end < len(indexed) and indexed[end][1] == indexed[pos][1]:
            end += 1
        avg_rank = (pos + 1 + end) / 2.0
        for k in range(pos, end):
            ranks[indexed[k][0]] = avg_rank
        pos = end
    return ranks


def wilcoxon_signed_rank(xs: list[float], ys: list[float]) -> dict[str, float]:
    diffs = [x - y for x, y in zip(xs, ys) if abs(x - y) > 0.0]
    n = len(diffs)
    if n == 0:
        return {"n": 0, "statistic": 0.0, "p_value": 1.0}
    abs_diffs = [abs(d) for d in diffs]
    ranks = ranks_with_average_ties(abs_diffs)
    w_plus = sum(rank for rank, diff in zip(ranks, diffs) if diff > 0)
    w_minus = sum(rank for rank, diff in zip(ranks, diffs) if diff < 0)
    statistic = min(w_plus, w_minus)
    mean = n * (n + 1) / 4.0
    variance = n * (n + 1) * (2 * n + 1) / 24.0
    if variance <= 0:
        p_value = 1.0
    else:
        z = (w_plus - mean) / math.sqrt(variance)
        p_value = math.erfc(abs(z) / math.sqrt(2.0))
    return {"n": n, "statistic": statistic, "p_value": min(1.0, max(0.0, p_value))}


def holm_bonferroni(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=lambda idx: p_values[idx])
    adjusted = [1.0] * len(p_values)
    previous = 0.0
    m = len(p_values)
    for rank, idx in enumerate(order):
        value = min(1.0, max(previous, (m - rank) * p_values[idx]))
        adjusted[idx] = value
        previous = value
    return adjusted


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute daily-window CI and paired tests.")
    parser.add_argument("--daily-metrics", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--bootstrap-samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    daily_path = Path(args.daily_metrics)
    out_dir = Path(args.out_dir) if args.out_dir else daily_path.parent
    rows = read_csv(daily_path)
    fixed = [
        row
        for row in rows
        if abs(f(row, "loss_rate") - 0.0) < 1e-12 and abs(f(row, "tau") - 0.40) < 1e-12
    ]

    by_method: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in fixed:
        by_method[str(row.get("method", ""))].append(row)

    ci_rows: list[dict[str, object]] = []
    for method, method_rows in sorted(by_method.items()):
        for metric in ["precision", "recall", "f1"]:
            low, high = bootstrap_count_ci(method_rows, metric, args.bootstrap_samples, args.seed)
            ci_rows.append(
                {
                    "method": method,
                    "metric": metric,
                    "n_windows": len(method_rows),
                    "estimate": metric_from_counts(method_rows, metric),
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )

    by_config: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if abs(f(row, "tau") - 0.40) < 1e-12:
            key = (str(row.get("method", "")), str(row.get("tau", "")), str(row.get("loss_rate", "")))
            by_config[key].append(row)

    ci_config_rows: list[dict[str, object]] = []
    for (method, tau, loss_rate), config_rows in sorted(by_config.items()):
        for metric in ["precision", "recall", "f1"]:
            low, high = bootstrap_count_ci(config_rows, metric, args.bootstrap_samples, args.seed)
            ci_config_rows.append(
                {
                    "method": method,
                    "tau": tau,
                    "loss_rate": loss_rate,
                    "metric": metric,
                    "n_windows": len(config_rows),
                    "estimate": metric_from_counts(config_rows, metric),
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )

    baseline = {
        (row.get("day", ""), row.get("delay_profile_sec", "")): f(row, "lookup_mean_ms")
        for row in fixed
        if row.get("method") == "linear_scan"
    }
    wilcoxon_rows: list[dict[str, object]] = []
    for method in sorted(method for method in by_method if method != "linear_scan"):
        xs: list[float] = []
        ys: list[float] = []
        for row in by_method[method]:
            key = (row.get("day", ""), row.get("delay_profile_sec", ""))
            if key in baseline:
                xs.append(baseline[key])
                ys.append(f(row, "lookup_mean_ms"))
        result = wilcoxon_signed_rank(xs, ys)
        wilcoxon_rows.append(
            {
                "method": method,
                "baseline": "linear_scan",
                "metric": "lookup_mean_ms",
                "n_pairs": int(result["n"]),
                "statistic": result["statistic"],
                "p_value": result["p_value"],
                "baseline_mean": statistics.fmean(xs) if xs else 0.0,
                "method_mean": statistics.fmean(ys) if ys else 0.0,
            }
        )

    adjusted = holm_bonferroni([f(row, "p_value") for row in wilcoxon_rows])
    for row, p_adj in zip(wilcoxon_rows, adjusted):
        row["holm_p_value"] = p_adj

    payload = {
        "daily_metrics": str(daily_path),
        "bootstrap_samples": args.bootstrap_samples,
        "ci": ci_rows,
        "ci_by_config": ci_config_rows,
        "wilcoxon": wilcoxon_rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "full_label_daily_stats.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(out_dir / "full_label_daily_stats_ci.csv", ci_rows)
    write_csv(out_dir / "full_label_daily_stats_ci_by_config.csv", ci_config_rows)
    write_csv(out_dir / "full_label_daily_stats_wilcoxon.csv", wilcoxon_rows)
    print(f"wrote daily stats to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
