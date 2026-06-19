#!/usr/bin/env python3
"""Learn fast Logistic Regression risk weights and a validation threshold.

The learned model is intentionally small enough for live scoring:

    logit = intercept
            + w_addr * s_addr
            + w_type * s_addr_x_type
            + w_token * s_addr_x_token

At runtime the detector can compare the logit directly with logit_tau, so the
hot path does not need to compute a sigmoid unless it wants calibrated
probabilities for display.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np
import pyarrow.parquet as pq


FAST_FEATURES = [
    "s_addr",
    "s_addr_x_type",
    "s_addr_x_token",
]


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


def x_y(df, feature_columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    x = df[feature_columns].to_numpy(dtype=np.float64, copy=False)
    y = df["label"].to_numpy(dtype=np.int8, copy=False)
    return x, y


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
        key = (
            float(item["f1"]),
            float(item["precision"]),
            float(item["recall"]),
            -float(item["threshold"]),
        )
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


def train_lr(args, files: list[Path], feature_columns: list[str]):
    try:
        from sklearn.linear_model import SGDClassifier
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("scikit-learn is required. Install python/requirements.txt.") from exc

    columns = feature_columns + ["label", args.split_column]
    scaler = StandardScaler()
    train_counts: Counter[int] = Counter()
    for _, df in iter_batches(files, columns, args.batch_size):
        mask = split_mask(df, args.split_column, "train")
        if not mask.any():
            continue
        x, y = x_y(df.loc[mask], feature_columns)
        scaler.partial_fit(x)
        train_counts.update(int(v) for v in y)

    if train_counts[0] == 0 or train_counts[1] == 0:
        raise RuntimeError(f"training split must contain both classes, got {dict(train_counts)}")
    total = train_counts[0] + train_counts[1]
    class_weights = {
        0: total / (2.0 * train_counts[0]),
        1: total / (2.0 * train_counts[1]),
    }
    print(f"[fit] train_counts={dict(train_counts)} class_weights={class_weights}", flush=True)

    clf = SGDClassifier(
        loss="log_loss",
        penalty="elasticnet",
        alpha=args.alpha,
        l1_ratio=args.l1_ratio,
        random_state=args.seed,
        learning_rate="optimal",
    )
    classes = np.asarray([0, 1], dtype=np.int8)
    for epoch in range(args.epochs):
        seen = 0
        positives = 0
        for _, df in iter_batches(files, columns, args.batch_size):
            mask = split_mask(df, args.split_column, "train")
            if not mask.any():
                continue
            x, y = x_y(df.loc[mask], feature_columns)
            sample_weight = np.where(y == 1, class_weights[1], class_weights[0])
            clf.partial_fit(scaler.transform(x), y, classes=classes, sample_weight=sample_weight)
            seen += int(y.size)
            positives += int(y.sum())
        print(f"[fit] epoch={epoch + 1}/{args.epochs} rows={seen:,} positives={positives:,}", flush=True)
    return {"model": clf, "scaler": scaler, "class_weights": class_weights}


def probability_histograms(args, files: list[Path], feature_columns: list[str], bundle, split_name: str):
    thresholds = np.linspace(0.0, 1.0, args.threshold_grid_size, dtype=np.float64)
    pos_hist = np.zeros(args.threshold_grid_size, dtype=np.int64)
    neg_hist = np.zeros(args.threshold_grid_size, dtype=np.int64)
    columns = feature_columns + ["label", args.split_column]
    clf = bundle["model"]
    scaler = bundle["scaler"]
    rows = 0
    positives = 0
    for _, df in iter_batches(files, columns, args.batch_size):
        mask = split_mask(df, args.split_column, split_name)
        if not mask.any():
            continue
        x, y = x_y(df.loc[mask], feature_columns)
        probs = clf.predict_proba(scaler.transform(x))[:, 1]
        idx = np.searchsorted(thresholds, probs, side="right") - 1
        idx = np.clip(idx, 0, args.threshold_grid_size - 1)
        pos_hist += np.bincount(idx[y == 1], minlength=args.threshold_grid_size)
        neg_hist += np.bincount(idx[y == 0], minlength=args.threshold_grid_size)
        rows += int(y.size)
        positives += int(y.sum())
    return thresholds, pos_hist, neg_hist, {"rows": rows, "positives": positives, "negatives": rows - positives}


def coefficient_report(bundle, feature_columns: list[str]) -> dict[str, object]:
    clf = bundle["model"]
    scaler = bundle["scaler"]
    coef_scaled = clf.coef_[0].astype(float)
    scale = np.asarray(scaler.scale_, dtype=np.float64)
    scale = np.where(scale == 0, 1.0, scale)
    coef_original = coef_scaled / scale
    intercept_original = float(clf.intercept_[0] - np.sum(coef_scaled * np.asarray(scaler.mean_, dtype=np.float64) / scale))
    rows = [
        {
            "feature": feature,
            "coef_scaled": float(coef_scaled[idx]),
            "coef_original_space": float(coef_original[idx]),
        }
        for idx, feature in enumerate(feature_columns)
    ]
    context_names = ["s_addr_x_type", "s_addr_x_token"]
    positive_context = {name: max(0.0, float(coef_original[feature_columns.index(name)])) for name in context_names}
    context_sum = sum(positive_context.values())
    if context_sum > 0:
        normalized_context = {name: value / context_sum for name, value in positive_context.items()}
    else:
        normalized_context = {name: 0.0 for name in context_names}
    return {
        "intercept_scaled": float(clf.intercept_[0]),
        "intercept_original_space": intercept_original,
        "coefficients": rows,
        "positive_context_weight_fraction": normalized_context,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Learn fast LR risk weights and validation tau.")
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
    args = parser.parse_args()

    feature_dir = Path(args.feature_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = iter_feature_files(feature_dir, args.max_files)
    print(f"[start] files={len(files)} feature_dir={feature_dir}", flush=True)

    bundle = train_lr(args, files, FAST_FEATURES)
    val_thresholds, val_pos, val_neg, val_summary = probability_histograms(args, files, FAST_FEATURES, bundle, "val")
    best_val = best_threshold_from_hist(val_thresholds, val_pos, val_neg)
    test_thresholds, test_pos, test_neg, test_summary = probability_histograms(args, files, FAST_FEATURES, bundle, "test")
    test_metrics = metrics_at_threshold_from_hist(test_thresholds, test_pos, test_neg, float(best_val["threshold"]))
    coefs = coefficient_report(bundle, FAST_FEATURES)
    tau_probability = float(best_val["threshold"])
    tau_logit = prob_to_logit(tau_probability)

    metrics = {
        "feature_dir": str(feature_dir),
        "split_column": args.split_column,
        "feature_columns": FAST_FEATURES,
        "model": "SGDClassifier(loss='log_loss')",
        "validation_summary": val_summary,
        "test_summary": test_summary,
        "validation": best_val,
        "test": test_metrics,
        "tau_probability": tau_probability,
        "tau_logit": tau_logit,
        "coefficients": coefs,
        "runtime_rule": {
            "score": "logit",
            "decision": "alert if logit >= tau_logit",
            "formula": "intercept + w_addr*s_addr + w_type*s_addr_x_type + w_token*s_addr_x_token",
        },
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with (out_dir / "fast_logistic_risk_model.pkl").open("wb") as handle:
        pickle.dump({"bundle": bundle, "feature_columns": FAST_FEATURES, "split_column": args.split_column}, handle)
    print(json.dumps({"validation": best_val, "test": test_metrics, "tau_logit": tau_logit, "coefficients": coefs}, indent=2), flush=True)
    print(f"[done] wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
