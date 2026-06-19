#!/usr/bin/env python3
"""Summarize repeated LR feature-ablation runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


METRIC_COLUMNS = [
    "validation_threshold",
    "validation_f1",
    "test_precision",
    "test_recall",
    "test_f1",
    "delta_f1_vs_full",
    "test_tp",
    "test_fp",
    "test_fn",
    "test_tn",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((value - mu) ** 2 for value in values) / (len(values) - 1))


def write_markdown(path: Path, rows: list[dict[str, object]]) -> None:
    lines = [
        "# LR Feature-Ablation 30-Run Summary",
        "",
        "| Variant | Runs | F1 mean | F1 std | Precision mean | Recall mean | Delta F1 mean |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {variant} | {runs} | {f1_mean:.9f} | {f1_std:.9f} | {precision_mean:.9f} | {recall_mean:.9f} | {delta_mean:.9f} |".format(
                variant=row["variant"],
                runs=int(row["runs"]),
                f1_mean=float(row["test_f1_mean"]),
                f1_std=float(row["test_f1_std"]),
                precision_mean=float(row["test_precision_mean"]),
                recall_mean=float(row["test_recall_mean"]),
                delta_mean=float(row["delta_f1_vs_full_mean"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize repeated LR ablation run directories.")
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    out_dir = Path(args.out_dir) if args.out_dir else runs_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    per_run_rows: list[dict[str, object]] = []
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for summary_path in sorted(runs_dir.glob("run_*/summary.csv")):
        run_name = summary_path.parent.name
        for row in read_csv(summary_path):
            variant = row["variant"]
            out_row: dict[str, object] = {"run": run_name, "variant": variant}
            for column in METRIC_COLUMNS:
                value = float(row[column])
                out_row[column] = value
                grouped[variant][column].append(value)
            per_run_rows.append(out_row)

    if not per_run_rows:
        raise FileNotFoundError(f"no run_*/summary.csv files found under {runs_dir}")

    summary_rows: list[dict[str, object]] = []
    for variant, metrics in grouped.items():
        item: dict[str, object] = {"variant": variant, "runs": len(metrics["test_f1"])}
        for column in METRIC_COLUMNS:
            item[f"{column}_mean"] = mean(metrics[column])
            item[f"{column}_std"] = std(metrics[column])
            item[f"{column}_min"] = min(metrics[column])
            item[f"{column}_max"] = max(metrics[column])
        summary_rows.append(item)
    summary_rows.sort(key=lambda row: float(row["test_f1_mean"]), reverse=True)

    write_csv(out_dir / "summary_by_run.csv", per_run_rows)
    write_csv(out_dir / "summary_aggregate.csv", summary_rows)
    write_markdown(out_dir / "summary_aggregate.md", summary_rows)
    payload = {"runs_dir": str(runs_dir), "runs": len({row["run"] for row in per_run_rows}), "summary": summary_rows}
    (out_dir / "summary_aggregate.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
