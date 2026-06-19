#!/usr/bin/env python3
"""Train learned full-risk models from exported Parquet feature shards.

The script is Colab-friendly: Logistic Regression is trained with streaming
``SGDClassifier(loss="log_loss")`` over Parquet shards, while Random Forest is
optional and trained on a bounded stratified subset.
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


FEATURE_COLUMNS = [
    "s_addr",
    "s_type",
    "s_token",
    "s_time",
    "s_value",
    "matched_prefix",
    "matched_suffix",
    "candidates_scored",
    "found_candidate",
    "s_addr_x_type",
    "s_addr_x_token",
    "s_addr_x_time",
    "s_addr_x_value",
    "address_gate_weak",
    "address_gate_strong",
]
MANUAL_WEIGHTS = np.asarray([0.30, 0.20, 0.20, 0.15, 0.15], dtype=np.float64)
NO_TIME_WEIGHTS = np.asarray([0.30, 0.20, 0.20, 0.0, 0.15], dtype=np.float64)
NO_TIME_WEIGHTS = NO_TIME_WEIGHTS / NO_TIME_WEIGHTS.sum()


CONTEXT_SCORE_SPECS: tuple[dict[str, object], ...] = (
    {"name": "ctx_gate_sum_symbolic_30", "kind": "gate", "addr": "sum", "base": 0.30, "type": 0.65, "token": 0.35, "time": 0.0, "value": 0.0},
    {"name": "ctx_gate_sum_symbolic_30_no_type", "kind": "gate", "addr": "sum", "base": 0.30, "type": 0.0, "token": 0.35, "time": 0.0, "value": 0.0},
    {"name": "ctx_gate_sum_symbolic_30_no_token", "kind": "gate", "addr": "sum", "base": 0.30, "type": 0.65, "token": 0.0, "time": 0.0, "value": 0.0},
    {"name": "ctx_boost_ttv_weak", "kind": "boost", "type": 0.05, "token": 0.05, "time": 0.0, "value": 0.025},
    {"name": "ctx_boost_ttv_mid", "kind": "boost", "type": 0.10, "token": 0.10, "time": 0.0, "value": 0.05},
    {"name": "ctx_boost_ttv_strong", "kind": "boost", "type": 0.20, "token": 0.20, "time": 0.0, "value": 0.10},
    {"name": "ctx_boost_tttv_weak", "kind": "boost", "type": 0.05, "token": 0.05, "time": 0.025, "value": 0.025},
    {"name": "ctx_boost_type_only", "kind": "boost", "type": 0.15, "token": 0.0, "time": 0.0, "value": 0.0},
    {"name": "ctx_boost_token_only", "kind": "boost", "type": 0.0, "token": 0.15, "time": 0.0, "value": 0.0},
    {"name": "ctx_mult_ttv_weak", "kind": "multiplicative", "type": 0.10, "token": 0.10, "time": 0.0, "value": 0.05},
    {"name": "ctx_mult_ttv_mid", "kind": "multiplicative", "type": 0.20, "token": 0.20, "time": 0.0, "value": 0.10},
    {"name": "ctx_mult_tttv_weak", "kind": "multiplicative", "type": 0.10, "token": 0.10, "time": 0.05, "value": 0.05},
    {"name": "ctx_gate_ttv_90", "kind": "gate", "base": 0.90, "type": 0.4, "token": 0.4, "time": 0.0, "value": 0.2},
    {"name": "ctx_gate_ttv_85", "kind": "gate", "base": 0.85, "type": 0.4, "token": 0.4, "time": 0.0, "value": 0.2},
    {"name": "ctx_gate_ttv_80", "kind": "gate", "base": 0.80, "type": 0.4, "token": 0.4, "time": 0.0, "value": 0.2},
    {"name": "ctx_gate_ttv_75", "kind": "gate", "base": 0.75, "type": 0.4, "token": 0.4, "time": 0.0, "value": 0.2},
    {"name": "ctx_gate_type_token_85", "kind": "gate", "base": 0.85, "type": 0.5, "token": 0.5, "time": 0.0, "value": 0.0},
    {"name": "ctx_gate_token_85", "kind": "gate", "base": 0.85, "type": 0.0, "token": 1.0, "time": 0.0, "value": 0.0},
    {"name": "ctx_gate_balanced_tuned_30", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.20, "token": 0.55, "time": 0.0, "value": 0.0},
    {"name": "ctx_gate_balanced_tuned_30_with_value", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.20, "token": 0.55, "time": 0.0, "value": 0.05},
    {"name": "ctx_gate_balanced_tuned_30_with_time", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.20, "token": 0.55, "time": 0.20, "value": 0.05},
    {"name": "ctx_gate_balanced_tuned_30_no_type", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.0, "token": 0.55, "time": 0.20, "value": 0.05},
    {"name": "ctx_gate_balanced_tuned_30_no_token", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.20, "token": 0.0, "time": 0.20, "value": 0.05},
    {"name": "ctx_gate_balanced_tuned_30_no_time", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.20, "token": 0.55, "time": 0.0, "value": 0.05},
    {"name": "ctx_gate_balanced_tuned_30_no_value", "kind": "gate", "addr": "balanced", "base": 0.30, "type": 0.20, "token": 0.55, "time": 0.20, "value": 0.0},
    {"name": "ctx_gate_temporal_80", "kind": "gate", "base": 0.80, "type": 0.35, "token": 0.35, "time": 0.15, "value": 0.15},
    {"name": "ctx_gate_temporal_no_type", "kind": "gate", "base": 0.80, "type": 0.0, "token": 0.35, "time": 0.15, "value": 0.15},
    {"name": "ctx_gate_temporal_no_token", "kind": "gate", "base": 0.80, "type": 0.35, "token": 0.0, "time": 0.15, "value": 0.15},
    {"name": "ctx_gate_temporal_no_time", "kind": "gate", "base": 0.80, "type": 0.35, "token": 0.35, "time": 0.0, "value": 0.15},
    {"name": "ctx_gate_temporal_no_value", "kind": "gate", "base": 0.80, "type": 0.35, "token": 0.35, "time": 0.15, "value": 0.0},
)


def iter_feature_files(feature_dir: Path, max_files: int = 0) -> list[Path]:
    files = sorted(feature_dir.glob("part-*.parquet"))
    if max_files > 0:
        files = files[:max_files]
    if not files:
        raise FileNotFoundError(f"no part-*.parquet files found in {feature_dir}")
    return files


def iter_batches(files: Iterable[Path], columns: list[str], batch_size: int):
    for path in files:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
            yield path, batch.to_pandas()


def split_mask(df, split_column: str, split_name: str):
    return df[split_column].astype(str).to_numpy() == split_name


def x_y(df):
    x = df[FEATURE_COLUMNS].to_numpy(dtype=np.float64, copy=True)
    y = df["label"].to_numpy(dtype=np.int8, copy=False)
    return x, y


def precision_recall_f1(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def metrics_from_scores(scores: np.ndarray, labels: np.ndarray, threshold: float) -> dict[str, float | int]:
    pred = scores >= threshold
    truth = labels.astype(bool)
    tp = int(np.logical_and(pred, truth).sum())
    fp = int(np.logical_and(pred, ~truth).sum())
    fn = int(np.logical_and(~pred, truth).sum())
    tn = int(np.logical_and(~pred, ~truth).sum())
    out: dict[str, float | int] = precision_recall_f1(tp, fp, fn)
    out.update({"threshold": float(threshold), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "rows": int(labels.size)})
    return out


def metrics_from_counts(threshold: float, tp: int, fp: int, fn: int, tn: int) -> dict[str, float | int]:
    out: dict[str, float | int] = precision_recall_f1(tp, fp, fn)
    out.update({"threshold": float(threshold), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "rows": int(tp + fp + fn + tn)})
    return out


def choose_threshold(scores: np.ndarray, labels: np.ndarray, grid_size: int) -> dict[str, float | int]:
    if scores.size == 0:
        return {"threshold": 0.5, "precision": 0.0, "recall": 0.0, "f1": 0.0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "rows": 0}
    thresholds = np.linspace(0.0, 1.0, grid_size)
    best = None
    for threshold in thresholds:
        item = metrics_from_scores(scores, labels, float(threshold))
        if best is None:
            best = item
            continue
        key = (float(item["f1"]), float(item["precision"]), float(item["recall"]), -float(item["threshold"]))
        best_key = (float(best["f1"]), float(best["precision"]), float(best["recall"]), -float(best["threshold"]))
        if key > best_key:
            best = item
    assert best is not None
    return best


def best_threshold_from_hist(thresholds: np.ndarray, pos_hist: np.ndarray, neg_hist: np.ndarray) -> dict[str, float | int]:
    total_pos = int(pos_hist.sum())
    total_neg = int(neg_hist.sum())
    pred_pos_pos = np.cumsum(pos_hist[::-1])[::-1]
    pred_pos_neg = np.cumsum(neg_hist[::-1])[::-1]
    best = None
    for i, threshold in enumerate(thresholds):
        tp = int(pred_pos_pos[i])
        fp = int(pred_pos_neg[i])
        fn = total_pos - tp
        tn = total_neg - fp
        item = metrics_from_counts(float(threshold), tp, fp, fn, tn)
        key = (float(item["f1"]), float(item["precision"]), float(item["recall"]), -float(item["threshold"]))
        if best is None:
            best = item
            continue
        best_key = (float(best["f1"]), float(best["precision"]), float(best["recall"]), -float(best["threshold"]))
        if key > best_key:
            best = item
    assert best is not None
    return best


def metrics_at_threshold_from_hist(thresholds: np.ndarray, pos_hist: np.ndarray, neg_hist: np.ndarray, threshold: float) -> dict[str, float | int]:
    idx = int(np.searchsorted(thresholds, threshold, side="left"))
    idx = min(max(idx, 0), thresholds.size - 1)
    pred_pos_pos = np.cumsum(pos_hist[::-1])[::-1]
    pred_pos_neg = np.cumsum(neg_hist[::-1])[::-1]
    tp = int(pred_pos_pos[idx])
    fp = int(pred_pos_neg[idx])
    fn = int(pos_hist.sum()) - tp
    tn = int(neg_hist.sum()) - fp
    return metrics_from_counts(float(thresholds[idx]), tp, fp, fn, tn)


def manual_scores(df) -> np.ndarray:
    components = df[["s_addr", "s_type", "s_token", "s_time", "s_value"]].to_numpy(dtype=np.float64, copy=False)
    return components @ MANUAL_WEIGHTS


def no_time_scores(df) -> np.ndarray:
    components = df[["s_addr", "s_type", "s_token", "s_time", "s_value"]].to_numpy(dtype=np.float64, copy=False)
    return components @ NO_TIME_WEIGHTS


def address_only_scores(df) -> np.ndarray:
    return df["s_addr"].to_numpy(dtype=np.float64, copy=False)


def balanced_address_scores(df) -> np.ndarray:
    raw = df["s_addr"].to_numpy(dtype=np.float64, copy=False)
    prefix = np.minimum(df["matched_prefix"].to_numpy(dtype=np.float64, copy=False), 6.0) / 6.0
    suffix = np.minimum(df["matched_suffix"].to_numpy(dtype=np.float64, copy=False), 6.0) / 6.0
    mx = np.maximum(prefix, suffix)
    mn = np.minimum(prefix, suffix)
    balance = np.divide(mn, mx, out=np.zeros_like(mx), where=mx > 0)
    return raw * (0.50 + 0.50 * balance)


def context_modifier_scores(df, spec: dict[str, object]) -> np.ndarray:
    if spec.get("addr") == "balanced":
        s_addr = balanced_address_scores(df)
    else:
        s_addr = df["s_addr"].to_numpy(dtype=np.float64, copy=False)
    s_type = df["s_type"].to_numpy(dtype=np.float64, copy=False)
    s_token = df["s_token"].to_numpy(dtype=np.float64, copy=False)
    s_time = df["s_time"].to_numpy(dtype=np.float64, copy=False)
    s_value = df["s_value"].to_numpy(dtype=np.float64, copy=False)

    type_weight = float(spec["type"])
    token_weight = float(spec["token"])
    time_weight = float(spec["time"])
    value_weight = float(spec["value"])
    modifier = type_weight * s_type + token_weight * s_token + time_weight * s_time + value_weight * s_value
    if spec["kind"] == "multiplicative":
        scores = s_addr * (1.0 + modifier)
    elif spec["kind"] == "gate":
        total_weight = type_weight + token_weight + time_weight + value_weight
        conditioned_time = s_time * np.maximum(s_type, s_token)
        gated_modifier = type_weight * s_type + token_weight * s_token + time_weight * conditioned_time + value_weight * s_value
        context = gated_modifier / total_weight if total_weight > 0 else np.zeros_like(s_addr)
        base = float(spec["base"])
        scores = s_addr * (base + (1.0 - base) * context)
    else:
        # Context can only move a candidate through the address match gate.
        scores = s_addr + (s_addr * modifier)
    return np.clip(scores, 0.0, 1.0)


def score_families():
    yield "address_only", address_only_scores
    yield "address_only_balanced", balanced_address_scores
    yield "manual_full_score", manual_scores
    yield "manual_no_time", no_time_scores
    for spec in CONTEXT_SCORE_SPECS:
        yield str(spec["name"]), lambda df, spec=spec: context_modifier_scores(df, spec)


def score_histograms_for_split(
    files: list[Path],
    split_column: str,
    split_name: str,
    batch_size: int,
    grid_size: int,
    scorers,
):
    thresholds = np.linspace(0.0, 1.0, grid_size, dtype=np.float64)
    hists = {
        name: {
            "pos": np.zeros(grid_size, dtype=np.int64),
            "neg": np.zeros(grid_size, dtype=np.int64),
        }
        for name, _ in scorers
    }
    rows = 0
    positives = 0
    columns = list(set(FEATURE_COLUMNS + ["label", split_column]))
    for _, df in iter_batches(files, columns, batch_size):
        mask = split_mask(df, split_column, split_name)
        if not mask.any():
            continue
        part = df.loc[mask]
        labels = part["label"].to_numpy(dtype=np.int8, copy=False)
        rows += int(labels.size)
        positives += int(labels.sum())
        for name, scorer in scorers:
            scores = np.clip(np.asarray(scorer(part), dtype=np.float64), 0.0, 1.0)
            idx = np.searchsorted(thresholds, scores, side="right") - 1
            idx = np.clip(idx, 0, grid_size - 1)
            hists[name]["pos"] += np.bincount(idx[labels == 1], minlength=grid_size)
            hists[name]["neg"] += np.bincount(idx[labels == 0], minlength=grid_size)
    summary = {"rows": rows, "positives": positives, "negatives": rows - positives}
    return thresholds, hists, summary


def collect_scores(files: list[Path], split_column: str, split_name: str, batch_size: int, scorer) -> tuple[np.ndarray, np.ndarray]:
    scores: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    columns = list(set(FEATURE_COLUMNS + ["label", split_column]))
    for _, df in iter_batches(files, columns, batch_size):
        mask = split_mask(df, split_column, split_name)
        if not mask.any():
            continue
        part = df.loc[mask]
        scores.append(np.asarray(scorer(part), dtype=np.float64))
        labels.append(part["label"].to_numpy(dtype=np.int8, copy=False))
    if not scores:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.int8)
    return np.concatenate(scores), np.concatenate(labels)


def train_logistic_sgd(args, files: list[Path], split_column: str):
    try:
        from sklearn.linear_model import SGDClassifier
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:  # pragma: no cover - exercised in Colab/local env
        raise RuntimeError("scikit-learn is required for training. Install python/requirements.txt.") from exc

    scaler = StandardScaler()
    columns = FEATURE_COLUMNS + ["label", split_column]
    train_counts: Counter[int] = Counter()
    for _, df in iter_batches(files, columns, args.batch_size):
        mask = split_mask(df, split_column, "train")
        if not mask.any():
            continue
        x, y = x_y(df.loc[mask])
        scaler.partial_fit(x)
        train_counts.update(int(v) for v in y)

    if train_counts[0] == 0 or train_counts[1] == 0:
        raise RuntimeError(f"training split must contain both classes, got {dict(train_counts)}")
    train_total = train_counts[0] + train_counts[1]
    class_weights = {
        0: train_total / (2.0 * train_counts[0]),
        1: train_total / (2.0 * train_counts[1]),
    }
    print(f"[lr] train class counts={dict(train_counts)} weights={class_weights}", flush=True)

    clf = SGDClassifier(
        loss="log_loss",
        penalty="elasticnet",
        alpha=args.lr_alpha,
        l1_ratio=args.lr_l1_ratio,
        random_state=args.seed,
        learning_rate="optimal",
    )
    classes = np.asarray([0, 1], dtype=np.int8)
    for epoch in range(args.epochs):
        seen = 0
        positives = 0
        for _, df in iter_batches(files, columns, args.batch_size):
            mask = split_mask(df, split_column, "train")
            if not mask.any():
                continue
            x, y = x_y(df.loc[mask])
            x = scaler.transform(x)
            sample_weight = np.where(y == 1, class_weights[1], class_weights[0])
            clf.partial_fit(x, y, classes=classes, sample_weight=sample_weight)
            seen += int(y.size)
            positives += int(y.sum())
        print(f"[lr] epoch={epoch + 1}/{args.epochs} rows={seen:,} positives={positives:,}", flush=True)
    return {"model": clf, "scaler": scaler, "class_weights": class_weights}


def predict_logistic(model_bundle, files: list[Path], split_column: str, split_name: str, batch_size: int) -> tuple[np.ndarray, np.ndarray]:
    columns = FEATURE_COLUMNS + ["label", split_column]
    scores: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    clf = model_bundle["model"]
    scaler = model_bundle["scaler"]
    for _, df in iter_batches(files, columns, batch_size):
        mask = split_mask(df, split_column, split_name)
        if not mask.any():
            continue
        x, y = x_y(df.loc[mask])
        prob = clf.predict_proba(scaler.transform(x))[:, 1]
        scores.append(prob)
        labels.append(y)
    if not scores:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.int8)
    return np.concatenate(scores), np.concatenate(labels)


def sample_for_random_forest(args, files: list[Path], split_column: str):
    rng = np.random.default_rng(args.seed)
    max_pos = max(1, args.rf_max_samples // 2)
    max_neg = max(1, args.rf_max_samples - max_pos)
    pos_x: list[np.ndarray] = []
    neg_x: list[np.ndarray] = []
    pos_seen = 0
    neg_seen = 0
    columns = FEATURE_COLUMNS + ["label", split_column]
    for _, df in iter_batches(files, columns, args.batch_size):
        mask = split_mask(df, split_column, "train")
        if not mask.any():
            continue
        x, y = x_y(df.loc[mask])
        for cls, target, max_count in ((1, pos_x, max_pos), (0, neg_x, max_neg)):
            cls_idx = np.flatnonzero(y == cls)
            if cls_idx.size == 0:
                continue
            seen_before = pos_seen if cls == 1 else neg_seen
            if cls == 1:
                pos_seen += int(cls_idx.size)
            else:
                neg_seen += int(cls_idx.size)
            keep_prob = min(1.0, max_count / max(1, seen_before + cls_idx.size))
            chosen = cls_idx[rng.random(cls_idx.size) < keep_prob]
            if chosen.size:
                target.append(x[chosen])
    if not pos_x or not neg_x:
        raise RuntimeError("not enough samples to train random forest")
    x_pos = np.concatenate(pos_x, axis=0)
    x_neg = np.concatenate(neg_x, axis=0)
    if x_pos.shape[0] > max_pos:
        x_pos = x_pos[rng.choice(x_pos.shape[0], size=max_pos, replace=False)]
    if x_neg.shape[0] > max_neg:
        x_neg = x_neg[rng.choice(x_neg.shape[0], size=max_neg, replace=False)]
    x = np.concatenate([x_pos, x_neg], axis=0)
    y = np.concatenate([np.ones(x_pos.shape[0], dtype=np.int8), np.zeros(x_neg.shape[0], dtype=np.int8)])
    order = rng.permutation(y.size)
    return x[order], y[order]


def train_random_forest(args, files: list[Path], split_column: str):
    try:
        from sklearn.ensemble import RandomForestClassifier
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("scikit-learn is required for Random Forest.") from exc
    x, y = sample_for_random_forest(args, files, split_column)
    clf = RandomForestClassifier(
        n_estimators=args.rf_estimators,
        max_depth=args.rf_max_depth if args.rf_max_depth > 0 else None,
        min_samples_leaf=args.rf_min_samples_leaf,
        n_jobs=-1,
        random_state=args.seed,
        class_weight="balanced_subsample",
    )
    clf.fit(x, y)
    print(f"[rf] trained on rows={y.size:,} positives={int(y.sum()):,}", flush=True)
    return {"model": clf, "sample_rows": int(y.size), "sample_positives": int(y.sum())}


def predict_random_forest(model_bundle, files: list[Path], split_column: str, split_name: str, batch_size: int) -> tuple[np.ndarray, np.ndarray]:
    columns = FEATURE_COLUMNS + ["label", split_column]
    scores: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    clf = model_bundle["model"]
    for _, df in iter_batches(files, columns, batch_size):
        mask = split_mask(df, split_column, split_name)
        if not mask.any():
            continue
        x, y = x_y(df.loc[mask])
        prob = clf.predict_proba(x)[:, 1]
        scores.append(prob)
        labels.append(y)
    if not scores:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.int8)
    return np.concatenate(scores), np.concatenate(labels)


def evaluate_named_scores(files: list[Path], split_column: str, batch_size: int, grid_size: int, fixed_threshold: float) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    scorers = list(score_families())
    val_thresholds, val_hists, val_summary = score_histograms_for_split(files, split_column, "val", batch_size, grid_size, scorers)
    test_thresholds, test_hists, test_summary = score_histograms_for_split(files, split_column, "test", batch_size, grid_size, scorers)
    for name, _ in scorers:
        best = best_threshold_from_hist(val_thresholds, val_hists[name]["pos"], val_hists[name]["neg"])
        test = metrics_at_threshold_from_hist(test_thresholds, test_hists[name]["pos"], test_hists[name]["neg"], float(best["threshold"]))
        fixed_val = metrics_at_threshold_from_hist(val_thresholds, val_hists[name]["pos"], val_hists[name]["neg"], fixed_threshold)
        fixed_test = metrics_at_threshold_from_hist(test_thresholds, test_hists[name]["pos"], test_hists[name]["neg"], fixed_threshold)
        out[name] = {"validation": best, "test": test, "fixed_threshold_validation": fixed_val, "fixed_threshold_test": fixed_test}
    out["_summary"] = {"validation": val_summary, "test": test_summary}
    return out


def evaluate_model(name: str, val_scores: np.ndarray, val_labels: np.ndarray, test_scores: np.ndarray, test_labels: np.ndarray, grid_size: int):
    best = choose_threshold(val_scores, val_labels, grid_size)
    test = metrics_from_scores(test_scores, test_labels, float(best["threshold"]))
    return {name: {"validation": best, "test": test}}


def logistic_coefficients(model_bundle) -> dict[str, object]:
    clf = model_bundle["model"]
    scaler = model_bundle["scaler"]
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
        for idx, feature in enumerate(FEATURE_COLUMNS)
    ]
    rows.sort(key=lambda item: abs(float(item["coef_scaled"])), reverse=True)
    return {
        "intercept_scaled": float(clf.intercept_[0]),
        "intercept_original_space": intercept_original,
        "coefficients": rows,
    }


def summarize_rows(files: list[Path], split_column: str, batch_size: int) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {split: Counter() for split in ("train", "val", "test")}
    for _, df in iter_batches(files, ["label", "found_candidate", split_column], batch_size):
        for split in counts:
            mask = split_mask(df, split_column, split)
            if not mask.any():
                continue
            labels = df.loc[mask, "label"].to_numpy(dtype=np.int8, copy=False)
            found = df.loc[mask, "found_candidate"].to_numpy(dtype=np.int8, copy=False).astype(bool)
            positive = labels == 1
            negative = ~positive
            counts[split]["rows"] += int(labels.size)
            counts[split]["positives"] += int(labels.sum())
            counts[split]["negatives"] += int(labels.size - labels.sum())
            counts[split]["found_candidate_rows"] += int(found.sum())
            counts[split]["no_candidate_rows"] += int(labels.size - found.sum())
            counts[split]["positive_found_candidate"] += int(np.logical_and(positive, found).sum())
            counts[split]["positive_no_candidate"] += int(np.logical_and(positive, ~found).sum())
            counts[split]["negative_found_candidate"] += int(np.logical_and(negative, found).sum())
            counts[split]["negative_no_candidate"] += int(np.logical_and(negative, ~found).sum())
    return {split: dict(counter) for split, counter in counts.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Colab-ready learned risk-score models.")
    parser.add_argument("--feature-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--split-column", choices=["split_time", "split_victim"], default="split_time")
    parser.add_argument("--batch-size", type=int, default=250_000)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold-grid-size", type=int, default=1001)
    parser.add_argument("--fixed-threshold", type=float, default=0.40)
    parser.add_argument("--lr-alpha", type=float, default=1e-5)
    parser.add_argument("--lr-l1-ratio", type=float, default=0.05)
    parser.add_argument("--skip-lr", action="store_true")
    parser.add_argument("--skip-rf", action="store_true")
    parser.add_argument("--rf-max-samples", type=int, default=1_000_000)
    parser.add_argument("--rf-estimators", type=int, default=200)
    parser.add_argument("--rf-max-depth", type=int, default=18)
    parser.add_argument("--rf-min-samples-leaf", type=int, default=20)
    args = parser.parse_args()

    feature_dir = Path(args.feature_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = iter_feature_files(feature_dir, args.max_files)
    print(f"using {len(files)} parquet feature shards from {feature_dir}", flush=True)

    row_summary = summarize_rows(files, args.split_column, args.batch_size)
    baseline_scores = evaluate_named_scores(files, args.split_column, args.batch_size, args.threshold_grid_size, args.fixed_threshold)
    best_baseline_name, best_baseline_metrics = max(
        ((name, item) for name, item in baseline_scores.items() if not name.startswith("_")),
        key=lambda item: (
            float(item[1]["validation"]["f1"]),  # type: ignore[index]
            float(item[1]["validation"]["precision"]),  # type: ignore[index]
            float(item[1]["validation"]["recall"]),  # type: ignore[index]
        ),
    )
    metrics: dict[str, object] = {
        "feature_dir": str(feature_dir),
        "split_column": args.split_column,
        "feature_columns": FEATURE_COLUMNS,
        "fixed_threshold": args.fixed_threshold,
        "row_summary": row_summary,
        "baselines": baseline_scores,
        "best_baseline_by_validation": {"name": best_baseline_name, **best_baseline_metrics},
        "models": {},
    }

    if not args.skip_lr:
        lr_bundle = train_logistic_sgd(args, files, args.split_column)
        lr_val_scores, lr_val_labels = predict_logistic(lr_bundle, files, args.split_column, "val", args.batch_size)
        lr_test_scores, lr_test_labels = predict_logistic(lr_bundle, files, args.split_column, "test", args.batch_size)
        metrics["models"].update(evaluate_model("logistic_sgd", lr_val_scores, lr_val_labels, lr_test_scores, lr_test_labels, args.threshold_grid_size))  # type: ignore[union-attr]
        metrics["models"]["logistic_sgd"]["coefficients"] = logistic_coefficients(lr_bundle)  # type: ignore[index]
        with (out_dir / "logistic_sgd_model.pkl").open("wb") as handle:
            pickle.dump({"bundle": lr_bundle, "feature_columns": FEATURE_COLUMNS, "split_column": args.split_column}, handle)

    if not args.skip_rf:
        rf_bundle = train_random_forest(args, files, args.split_column)
        rf_val_scores, rf_val_labels = predict_random_forest(rf_bundle, files, args.split_column, "val", args.batch_size)
        rf_test_scores, rf_test_labels = predict_random_forest(rf_bundle, files, args.split_column, "test", args.batch_size)
        rf_eval = evaluate_model("random_forest_subset", rf_val_scores, rf_val_labels, rf_test_scores, rf_test_labels, args.threshold_grid_size)
        rf_eval["random_forest_subset"]["training_subset"] = {"rows": rf_bundle["sample_rows"], "positives": rf_bundle["sample_positives"]}
        metrics["models"].update(rf_eval)  # type: ignore[union-attr]
        with (out_dir / "random_forest_subset_model.pkl").open("wb") as handle:
            pickle.dump({"bundle": rf_bundle, "feature_columns": FEATURE_COLUMNS, "split_column": args.split_column}, handle)

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics["models"], indent=2), flush=True)
    print(f"wrote training outputs to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
