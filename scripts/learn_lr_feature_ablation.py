#!/usr/bin/env python3
"""Train calibrated Logistic Regression ablations for risk-score feature utility.

This script answers a different question from fixed-threshold deployment replay:
given the same train/validation/test split, how much test quality is lost when a
score component is removed and the ablated model is recalibrated on validation?
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pyarrow.parquet as pq


FeatureBuilder = Callable[[object], np.ndarray]

CANONICAL_VARIANTS = (
    "full_addr_type_token",
    "address_only",
    "no_type",
    "no_token",
    "prefix_only",
    "suffix_only",
)


def iter_feature_files(feature_dir: Path, max_files: int = 0) -> list[Path]:
    files = sorted(feature_dir.glob("part-*.parquet"))
    if max_files > 0:
        files = files[:max_files]
    if not files:
        raise FileNotFoundError(f"no part-*.parquet files found in {feature_dir}")
    return files


def iter_batches(paths: Iterable[Path], columns: list[str], batch_size: int):
    for path in paths:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
            yield path, batch.to_pandas()


def split_mask(df, split_column: str, split_name: str):
    return df[split_column].astype(str).to_numpy() == split_name


def col(df, name: str) -> np.ndarray:
    return df[name].to_numpy(dtype=np.float64, copy=False)


def prefix_addr(df) -> np.ndarray:
    return np.minimum(col(df, "matched_prefix"), 6.0) / 6.0


def suffix_addr(df) -> np.ndarray:
    return np.minimum(col(df, "matched_suffix"), 6.0) / 6.0


def both_addr(df) -> np.ndarray:
    return np.minimum(prefix_addr(df), suffix_addr(df))


def either_addr(df) -> np.ndarray:
    return np.maximum(prefix_addr(df), suffix_addr(df))


def balance_addr(df) -> np.ndarray:
    low = both_addr(df)
    high = either_addr(df)
    return np.divide(low, high, out=np.zeros_like(high), where=high > 0)


def prefix_suffix_gap(df) -> np.ndarray:
    return np.abs(prefix_addr(df) - suffix_addr(df))


def candidate_pressure(df) -> np.ndarray:
    candidates = col(df, "candidates_scored")
    # Cap the log scale so a few high-fanout wallets do not dominate the LR fit.
    return np.clip(np.log1p(candidates) / np.log1p(128.0), 0.0, 1.0)


def context_min(df) -> np.ndarray:
    return np.minimum(col(df, "s_type"), col(df, "s_token"))


def context_gap(df) -> np.ndarray:
    return np.abs(col(df, "s_type") - col(df, "s_token"))


def stack(*items: np.ndarray) -> np.ndarray:
    return np.column_stack(items).astype(np.float64, copy=False)


def variant_specs() -> dict[str, dict[str, object]]:
    return {
        "full_addr_type_token": {
            "description": "Final fast score: address plus address-gated type and token.",
            "columns": ["s_addr", "s_addr_x_type", "s_addr_x_token"],
            "builder": lambda df: stack(col(df, "s_addr"), col(df, "s_addr_x_type"), col(df, "s_addr_x_token")),
        },
        "full_addr_type_token_time": {
            "description": "Address-gated type/token plus recency evidence.",
            "columns": ["s_addr", "s_addr_x_type", "s_addr_x_token", "s_addr_x_time"],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr_x_time"),
            ),
        },
        "full_addr_type_token_value": {
            "description": "Address-gated type/token plus value-shape evidence.",
            "columns": ["s_addr", "s_addr_x_type", "s_addr_x_token", "s_addr_x_value"],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr_x_value"),
            ),
        },
        "full_addr_type_token_time_value": {
            "description": "Address-gated type/token plus recency and value-shape evidence.",
            "columns": ["s_addr", "s_addr_x_type", "s_addr_x_token", "s_addr_x_time", "s_addr_x_value"],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr_x_time"),
                col(df, "s_addr_x_value"),
            ),
        },
        "full_context_product": {
            "description": "Address-gated context with type-token product and agreement features.",
            "columns": [
                "s_addr",
                "s_addr_x_type",
                "s_addr_x_token",
                "s_addr_x_type_token",
                "s_addr_x_context_min",
                "s_addr_x_context_gap",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr") * col(df, "s_type") * col(df, "s_token"),
                col(df, "s_addr") * context_min(df),
                col(df, "s_addr") * context_gap(df),
            ),
        },
        "full_context_product_value": {
            "description": "Type-token agreement features plus value-shape evidence.",
            "columns": [
                "s_addr",
                "s_addr_x_type",
                "s_addr_x_token",
                "s_addr_x_type_token",
                "s_addr_x_context_min",
                "s_addr_x_context_gap",
                "s_addr_x_value",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr") * col(df, "s_type") * col(df, "s_token"),
                col(df, "s_addr") * context_min(df),
                col(df, "s_addr") * context_gap(df),
                col(df, "s_addr_x_value"),
            ),
        },
        "full_token_quadratic_value": {
            "description": "Address-gated type/token with token curvature and value-shape evidence.",
            "columns": [
                "s_addr",
                "s_addr_x_type",
                "s_addr_x_token",
                "s_addr_x_token2",
                "s_addr_x_type_token",
                "s_addr_x_value",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr") * col(df, "s_token") * col(df, "s_token"),
                col(df, "s_addr") * col(df, "s_type") * col(df, "s_token"),
                col(df, "s_addr_x_value"),
            ),
        },
        "full_hard_negative": {
            "description": "Full contextual score with ambiguity and display-balance features for hard negatives.",
            "columns": [
                "s_addr",
                "s_addr_x_type",
                "s_addr_x_token",
                "s_addr_x_time",
                "s_addr_x_value",
                "balance_addr",
                "both_addr",
                "prefix_suffix_gap",
                "candidate_pressure",
                "s_addr_x_candidate_pressure",
                "balance_x_type",
                "balance_x_token",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr_x_time"),
                col(df, "s_addr_x_value"),
                balance_addr(df),
                both_addr(df),
                prefix_suffix_gap(df),
                candidate_pressure(df),
                col(df, "s_addr") * candidate_pressure(df),
                balance_addr(df) * col(df, "s_type"),
                balance_addr(df) * col(df, "s_token"),
            ),
        },
        "full_hard_negative_no_candidate": {
            "description": "Hard-negative feature set without candidate-fanout evidence.",
            "columns": [
                "s_addr",
                "s_addr_x_type",
                "s_addr_x_token",
                "s_addr_x_time",
                "s_addr_x_value",
                "balance_addr",
                "both_addr",
                "prefix_suffix_gap",
                "balance_x_type",
                "balance_x_token",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                col(df, "s_addr_x_time"),
                col(df, "s_addr_x_value"),
                balance_addr(df),
                both_addr(df),
                prefix_suffix_gap(df),
                balance_addr(df) * col(df, "s_type"),
                balance_addr(df) * col(df, "s_token"),
            ),
        },
        "full_display_addr_type_token": {
            "description": "Display-aware full score: prefix/suffix/both-side address evidence plus address-gated type and token.",
            "columns": [
                "s_addr",
                "prefix_addr",
                "suffix_addr",
                "both_addr",
                "balance_addr",
                "s_addr_x_type",
                "s_addr_x_token",
                "prefix_x_type",
                "suffix_x_type",
                "both_x_type",
                "prefix_x_token",
                "suffix_x_token",
                "both_x_token",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                prefix_addr(df),
                suffix_addr(df),
                both_addr(df),
                balance_addr(df),
                col(df, "s_addr_x_type"),
                col(df, "s_addr_x_token"),
                prefix_addr(df) * col(df, "s_type"),
                suffix_addr(df) * col(df, "s_type"),
                both_addr(df) * col(df, "s_type"),
                prefix_addr(df) * col(df, "s_token"),
                suffix_addr(df) * col(df, "s_token"),
                both_addr(df) * col(df, "s_token"),
            ),
        },
        "display_address_only": {
            "description": "Display-aware address evidence only; no type/token context.",
            "columns": ["s_addr", "prefix_addr", "suffix_addr", "both_addr", "balance_addr"],
            "builder": lambda df: stack(col(df, "s_addr"), prefix_addr(df), suffix_addr(df), both_addr(df), balance_addr(df)),
        },
        "display_no_type": {
            "description": "Display-aware address plus token context; type evidence removed.",
            "columns": [
                "s_addr",
                "prefix_addr",
                "suffix_addr",
                "both_addr",
                "balance_addr",
                "s_addr_x_token",
                "prefix_x_token",
                "suffix_x_token",
                "both_x_token",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                prefix_addr(df),
                suffix_addr(df),
                both_addr(df),
                balance_addr(df),
                col(df, "s_addr_x_token"),
                prefix_addr(df) * col(df, "s_token"),
                suffix_addr(df) * col(df, "s_token"),
                both_addr(df) * col(df, "s_token"),
            ),
        },
        "display_no_token": {
            "description": "Display-aware address plus type context; token evidence removed.",
            "columns": [
                "s_addr",
                "prefix_addr",
                "suffix_addr",
                "both_addr",
                "balance_addr",
                "s_addr_x_type",
                "prefix_x_type",
                "suffix_x_type",
                "both_x_type",
            ],
            "builder": lambda df: stack(
                col(df, "s_addr"),
                prefix_addr(df),
                suffix_addr(df),
                both_addr(df),
                balance_addr(df),
                col(df, "s_addr_x_type"),
                prefix_addr(df) * col(df, "s_type"),
                suffix_addr(df) * col(df, "s_type"),
                both_addr(df) * col(df, "s_type"),
            ),
        },
        "address_only": {
            "description": "Address similarity only, validation-calibrated.",
            "columns": ["s_addr"],
            "builder": lambda df: stack(col(df, "s_addr")),
        },
        "no_type": {
            "description": "Address plus token context; type evidence removed.",
            "columns": ["s_addr", "s_addr_x_token"],
            "builder": lambda df: stack(col(df, "s_addr"), col(df, "s_addr_x_token")),
        },
        "no_token": {
            "description": "Address plus type context; token evidence removed.",
            "columns": ["s_addr", "s_addr_x_type"],
            "builder": lambda df: stack(col(df, "s_addr"), col(df, "s_addr_x_type")),
        },
        "prefix_only": {
            "description": "Prefix-side address evidence plus type and token context.",
            "columns": ["prefix_addr", "prefix_x_type", "prefix_x_token"],
            "builder": lambda df: stack(
                prefix_addr(df),
                prefix_addr(df) * col(df, "s_type"),
                prefix_addr(df) * col(df, "s_token"),
            ),
        },
        "suffix_only": {
            "description": "Suffix-side address evidence plus type and token context.",
            "columns": ["suffix_addr", "suffix_x_type", "suffix_x_token"],
            "builder": lambda df: stack(
                suffix_addr(df),
                suffix_addr(df) * col(df, "s_type"),
                suffix_addr(df) * col(df, "s_token"),
            ),
        },
        "type_token_without_address": {
            "description": "Context without address evidence, included as a sanity check.",
            "columns": ["s_type", "s_token"],
            "builder": lambda df: stack(col(df, "s_type"), col(df, "s_token")),
        },
    }


def metrics_from_counts(tp: int, fp: int, fn: int, tn: int, threshold: float) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": float(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "rows": int(tp + fp + fn + tn),
    }


def best_threshold_from_hist(thresholds: np.ndarray, pos_hist: np.ndarray, neg_hist: np.ndarray) -> dict[str, float | int]:
    total_pos = int(pos_hist.sum())
    total_neg = int(neg_hist.sum())
    tp_c = np.cumsum(pos_hist[::-1])[::-1]
    fp_c = np.cumsum(neg_hist[::-1])[::-1]
    best: dict[str, float | int] | None = None
    for i, threshold in enumerate(thresholds):
        tp = int(tp_c[i])
        fp = int(fp_c[i])
        fn = total_pos - tp
        tn = total_neg - fp
        item = metrics_from_counts(tp, fp, fn, tn, float(threshold))
        key = (float(item["f1"]), float(item["precision"]), float(item["recall"]), -float(item["threshold"]))
        if best is None or key > (
            float(best["f1"]),
            float(best["precision"]),
            float(best["recall"]),
            -float(best["threshold"]),
        ):
            best = item
    assert best is not None
    return best


def metrics_at_threshold_from_hist(
    thresholds: np.ndarray,
    pos_hist: np.ndarray,
    neg_hist: np.ndarray,
    threshold: float,
) -> dict[str, float | int]:
    idx = int(np.searchsorted(thresholds, threshold, side="left"))
    idx = max(0, min(idx, len(thresholds) - 1))
    tp = int(pos_hist[idx:].sum())
    fp = int(neg_hist[idx:].sum())
    fn = int(pos_hist[:idx].sum())
    tn = int(neg_hist[:idx].sum())
    return metrics_from_counts(tp, fp, fn, tn, float(thresholds[idx]))


def prob_to_logit(prob: float) -> float:
    eps = 1e-12
    prob = min(1.0 - eps, max(eps, prob))
    return math.log(prob / (1.0 - prob))


def coefficients_original_space(model, scaler, feature_names: list[str]) -> dict[str, object]:
    coef_scaled = model.coef_[0].astype(float)
    scale = np.asarray(scaler.scale_, dtype=np.float64)
    scale = np.where(scale == 0, 1.0, scale)
    coef_original = coef_scaled / scale
    intercept_original = float(model.intercept_[0] - np.sum(coef_scaled * np.asarray(scaler.mean_, dtype=np.float64) / scale))
    return {
        "intercept_scaled": float(model.intercept_[0]),
        "intercept_original_space": intercept_original,
        "coefficients": [
            {
                "feature": feature,
                "coef_scaled": float(coef_scaled[idx]),
                "coef_original_space": float(coef_original[idx]),
            }
            for idx, feature in enumerate(feature_names)
        ],
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, object]]) -> None:
    lines = [
        "# LR Feature-Utility Ablation",
        "",
        "Each variant is trained on the same training split, its threshold is selected on validation only, and the final numbers are measured on test.",
        "",
        "| Variant | Val tau | Test precision | Test recall | Test F1 | Delta F1 vs full |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {variant} | {tau:.3f} | {precision:.6f} | {recall:.6f} | {f1:.6f} | {delta:.6f} |".format(
                variant=row["variant"],
                tau=float(row["validation_threshold"]),
                precision=float(row["test_precision"]),
                recall=float(row["test_recall"]),
                f1=float(row["test_f1"]),
                delta=float(row["delta_f1_vs_full"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train LR feature ablations with validation-calibrated tau.")
    parser.add_argument("--feature-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--split-column", choices=["split_time", "split_victim"], default="split_time")
    parser.add_argument("--batch-size", type=int, default=200_000)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold-grid-size", type=int, default=1001)
    parser.add_argument("--alpha", type=float, default=1e-5)
    parser.add_argument("--l1-ratio", type=float, default=0.05)
    parser.add_argument("--include-display-experimental", action="store_true")
    parser.add_argument("--include-expanded-experimental", action="store_true")
    parser.add_argument("--variants", default="", help="Comma-separated variant names; overrides experimental/default selection.")
    args = parser.parse_args()

    try:
        from sklearn.linear_model import SGDClassifier
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("scikit-learn is required. Install python/requirements.txt.") from exc

    feature_dir = Path(args.feature_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = iter_feature_files(feature_dir, args.max_files)
    specs = variant_specs()
    if args.variants.strip():
        selected = [name.strip() for name in args.variants.split(",") if name.strip()]
        missing = [name for name in selected if name not in specs]
        if missing:
            raise ValueError(f"unknown variants: {', '.join(missing)}")
        specs = {name: specs[name] for name in selected}
    elif not args.include_display_experimental and not args.include_expanded_experimental:
        specs = {name: specs[name] for name in CANONICAL_VARIANTS}
    elif args.include_expanded_experimental and not args.include_display_experimental:
        expanded = (
            "full_addr_type_token",
            "full_addr_type_token_time",
            "full_addr_type_token_value",
            "full_addr_type_token_time_value",
            "full_context_product",
            "full_context_product_value",
            "full_token_quadratic_value",
            "full_hard_negative",
            "full_hard_negative_no_candidate",
            "address_only",
            "no_type",
            "no_token",
            "prefix_only",
            "suffix_only",
        )
        specs = {name: specs[name] for name in expanded}
    required_columns = sorted(
        {
            "label",
            args.split_column,
            "s_addr",
            "s_type",
            "s_token",
            "s_addr_x_type",
            "s_addr_x_token",
            "s_addr_x_time",
            "s_addr_x_value",
            "matched_prefix",
            "matched_suffix",
            "candidates_scored",
        }
    )
    print(f"[start] files={len(files)} variants={len(specs)} split={args.split_column}", flush=True)

    scalers = {name: StandardScaler() for name in specs}
    train_counts: Counter[int] = Counter()
    split_counts: dict[str, Counter[str]] = {split: Counter() for split in ("train", "val", "test")}
    for _, df in iter_batches(files, required_columns, args.batch_size):
        labels_all = df["label"].to_numpy(dtype=np.int8, copy=False)
        for split in split_counts:
            mask_split = split_mask(df, args.split_column, split)
            if mask_split.any():
                labels_split = labels_all[mask_split]
                split_counts[split]["rows"] += int(labels_split.size)
                split_counts[split]["positives"] += int(labels_split.sum())
                split_counts[split]["negatives"] += int(labels_split.size - labels_split.sum())
        mask = split_mask(df, args.split_column, "train")
        if not mask.any():
            continue
        labels = df.loc[mask, "label"].to_numpy(dtype=np.int8, copy=False)
        train_counts.update(int(v) for v in labels)
        train_df = df.loc[mask]
        for name, spec in specs.items():
            scalers[name].partial_fit(spec["builder"](train_df))  # type: ignore[index, operator]

    if train_counts[0] == 0 or train_counts[1] == 0:
        raise RuntimeError(f"training split must contain both classes, got {dict(train_counts)}")
    total = train_counts[0] + train_counts[1]
    class_weights = {0: total / (2.0 * train_counts[0]), 1: total / (2.0 * train_counts[1])}
    print(f"[fit] train_counts={dict(train_counts)} class_weights={class_weights}", flush=True)

    models = {
        name: SGDClassifier(
            loss="log_loss",
            penalty="elasticnet",
            alpha=args.alpha,
            l1_ratio=args.l1_ratio,
            random_state=args.seed,
            learning_rate="optimal",
        )
        for name in specs
    }
    classes = np.asarray([0, 1], dtype=np.int8)
    for epoch in range(args.epochs):
        seen = 0
        positives = 0
        for _, df in iter_batches(files, required_columns, args.batch_size):
            mask = split_mask(df, args.split_column, "train")
            if not mask.any():
                continue
            train_df = df.loc[mask]
            y = train_df["label"].to_numpy(dtype=np.int8, copy=False)
            sample_weight = np.where(y == 1, class_weights[1], class_weights[0])
            for name, spec in specs.items():
                x = spec["builder"](train_df)  # type: ignore[index, operator]
                models[name].partial_fit(scalers[name].transform(x), y, classes=classes, sample_weight=sample_weight)
            seen += int(y.size)
            positives += int(y.sum())
        print(f"[fit] epoch={epoch + 1}/{args.epochs} rows={seen:,} positives={positives:,}", flush=True)

    thresholds = np.linspace(0.0, 1.0, args.threshold_grid_size, dtype=np.float64)
    hists = {
        split: {
            name: {
                "pos": np.zeros(args.threshold_grid_size, dtype=np.int64),
                "neg": np.zeros(args.threshold_grid_size, dtype=np.int64),
            }
            for name in specs
        }
        for split in ("val", "test")
    }
    for _, df in iter_batches(files, required_columns, args.batch_size):
        for split in ("val", "test"):
            mask = split_mask(df, args.split_column, split)
            if not mask.any():
                continue
            split_df = df.loc[mask]
            y = split_df["label"].to_numpy(dtype=np.int8, copy=False)
            for name, spec in specs.items():
                x = spec["builder"](split_df)  # type: ignore[index, operator]
                probs = models[name].predict_proba(scalers[name].transform(x))[:, 1]
                idx = np.searchsorted(thresholds, probs, side="right") - 1
                idx = np.clip(idx, 0, args.threshold_grid_size - 1)
                hists[split][name]["pos"] += np.bincount(idx[y == 1], minlength=args.threshold_grid_size)
                hists[split][name]["neg"] += np.bincount(idx[y == 0], minlength=args.threshold_grid_size)

    variants: dict[str, dict[str, object]] = {}
    for name, spec in specs.items():
        validation = best_threshold_from_hist(thresholds, hists["val"][name]["pos"], hists["val"][name]["neg"])
        test = metrics_at_threshold_from_hist(thresholds, hists["test"][name]["pos"], hists["test"][name]["neg"], float(validation["threshold"]))
        variants[name] = {
            "description": spec["description"],
            "feature_columns": spec["columns"],
            "validation": validation,
            "test": test,
            "tau_logit": prob_to_logit(float(validation["threshold"])),
            "coefficients": coefficients_original_space(models[name], scalers[name], spec["columns"]),  # type: ignore[arg-type]
        }

    primary_variant = "full_addr_type_token"
    full_f1 = float(variants[primary_variant]["test"]["f1"])  # type: ignore[index]
    summary_rows: list[dict[str, object]] = []
    for name, item in sorted(variants.items(), key=lambda kv: float(kv[1]["test"]["f1"]), reverse=True):  # type: ignore[index]
        validation = item["validation"]  # type: ignore[assignment]
        test = item["test"]  # type: ignore[assignment]
        summary_rows.append(
            {
                "variant": name,
                "validation_threshold": validation["threshold"],  # type: ignore[index]
                "validation_f1": validation["f1"],  # type: ignore[index]
                "test_precision": test["precision"],  # type: ignore[index]
                "test_recall": test["recall"],  # type: ignore[index]
                "test_f1": test["f1"],  # type: ignore[index]
                "delta_f1_vs_full": float(test["f1"]) - full_f1,  # type: ignore[index]
                "test_tp": test["tp"],  # type: ignore[index]
                "test_fp": test["fp"],  # type: ignore[index]
                "test_fn": test["fn"],  # type: ignore[index]
                "test_tn": test["tn"],  # type: ignore[index]
            }
        )

    payload = {
        "feature_dir": str(feature_dir),
        "split_column": args.split_column,
        "row_summary": {split: dict(counts) for split, counts in split_counts.items()},
        "class_weights": class_weights,
        "model": "SGDClassifier(loss='log_loss')",
        "calibration_policy": "threshold chosen on validation split separately for each ablation, then frozen for test",
        "primary_variant": primary_variant,
        "variants": variants,
        "summary": summary_rows,
    }
    (out_dir / "metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(out_dir / "summary.csv", summary_rows)
    write_markdown(out_dir / "summary.md", summary_rows)
    with (out_dir / "lr_feature_ablation_models.pkl").open("wb") as handle:
        pickle.dump(
            {
                "models": models,
                "scalers": scalers,
                "variant_specs": {name: {"description": spec["description"], "columns": spec["columns"]} for name, spec in specs.items()},
                "split_column": args.split_column,
            },
            handle,
        )
    print(json.dumps({"summary": summary_rows}, indent=2), flush=True)
    print(f"[done] wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
