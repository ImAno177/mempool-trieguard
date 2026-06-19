#!/usr/bin/env python3
"""Search constrained risk-score formulas over exported feature shards.

The search is intentionally constrained: formulas keep address evidence as the
gate and only learn bounded context modifiers. This avoids free-form symbolic
expressions that are hard to defend in the paper.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq


FEATURE_COLUMNS = [
    "label",
    "split_time",
    "split_victim",
    "matched_prefix",
    "matched_suffix",
    "s_addr",
    "s_type",
    "s_token",
    "s_time",
    "s_value",
    "found_candidate",
]


@dataclass(frozen=True)
class FormulaSpec:
    name: str
    addr_mode: str
    beta: float
    w_type: float
    w_token: float
    w_value: float
    w_time: float
    time_mode: str
    alpha: float = 1.0
    gamma: float = 1.0


def iter_feature_files(feature_dir: Path, max_files: int) -> list[Path]:
    files = sorted(feature_dir.glob("part-*.parquet"))
    if max_files > 0:
        files = files[:max_files]
    if not files:
        raise FileNotFoundError(f"no part-*.parquet files found in {feature_dir}")
    return files


def load_split(files: list[Path], split_column: str, split_name: str, batch_size: int) -> dict[str, np.ndarray]:
    chunks: dict[str, list[np.ndarray]] = {
        "y": [],
        "mp": [],
        "ms": [],
        "s_addr": [],
        "s_type": [],
        "s_token": [],
        "s_time": [],
        "s_value": [],
        "found": [],
    }
    columns = FEATURE_COLUMNS
    rows = 0
    for path in files:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
            df = batch.to_pandas(self_destruct=True)
            mask = df[split_column].astype(str).to_numpy() == split_name
            if not mask.any():
                continue
            part = df.loc[mask]
            chunks["y"].append(part["label"].to_numpy(dtype=np.int8, copy=False))
            chunks["mp"].append(part["matched_prefix"].to_numpy(dtype=np.float32, copy=False))
            chunks["ms"].append(part["matched_suffix"].to_numpy(dtype=np.float32, copy=False))
            chunks["s_addr"].append(part["s_addr"].to_numpy(dtype=np.float32, copy=False))
            chunks["s_type"].append(part["s_type"].to_numpy(dtype=np.float32, copy=False))
            chunks["s_token"].append(part["s_token"].to_numpy(dtype=np.float32, copy=False))
            chunks["s_time"].append(part["s_time"].to_numpy(dtype=np.float32, copy=False))
            chunks["s_value"].append(part["s_value"].to_numpy(dtype=np.float32, copy=False))
            chunks["found"].append(part["found_candidate"].to_numpy(dtype=np.int8, copy=False))
            rows += int(mask.sum())
    if rows == 0:
        raise RuntimeError(f"split {split_name!r} is empty")
    return {name: np.concatenate(parts) for name, parts in chunks.items()}


def metrics_at_best_tau(scores: np.ndarray, y: np.ndarray, thresholds: np.ndarray) -> dict[str, float | int]:
    scores = np.clip(scores.astype(np.float64, copy=False), 0.0, 1.0)
    idx = np.searchsorted(thresholds, scores, side="right") - 1
    idx = np.clip(idx, 0, len(thresholds) - 1)
    pos_hist = np.bincount(idx[y == 1], minlength=len(thresholds))
    neg_hist = np.bincount(idx[y == 0], minlength=len(thresholds))
    total_pos = int(pos_hist.sum())
    total_neg = int(neg_hist.sum())
    tp_c = np.cumsum(pos_hist[::-1])[::-1]
    fp_c = np.cumsum(neg_hist[::-1])[::-1]
    best: dict[str, float | int] | None = None
    for i, tau in enumerate(thresholds):
        tp = int(tp_c[i])
        fp = int(fp_c[i])
        fn = total_pos - tp
        tn = total_neg - fp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        item: dict[str, float | int] = {
            "tau": float(tau),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "rows": total_pos + total_neg,
        }
        key = (f1, precision, recall, -float(tau))
        if best is None or key > (float(best["f1"]), float(best["precision"]), float(best["recall"]), -float(best["tau"])):
            best = item
    assert best is not None
    return best


def prefix_score(data: dict[str, np.ndarray]) -> np.ndarray:
    return np.minimum(data["mp"], 6.0) / 6.0


def suffix_score(data: dict[str, np.ndarray]) -> np.ndarray:
    return np.minimum(data["ms"], 6.0) / 6.0


def address_score(data: dict[str, np.ndarray], spec: FormulaSpec) -> np.ndarray:
    pref = prefix_score(data)
    suff = suffix_score(data)
    summed = data["s_addr"].astype(np.float32, copy=False)
    mx = np.maximum(pref, suff)
    mn = np.minimum(pref, suff)
    if spec.addr_mode == "sum":
        return summed
    if spec.addr_mode == "prefix":
        return pref
    if spec.addr_mode == "suffix":
        return suff
    if spec.addr_mode == "min":
        return mn
    if spec.addr_mode == "harmonic":
        return np.divide(2.0 * pref * suff, pref + suff, out=np.zeros_like(pref), where=(pref + suff) > 0)
    if spec.addr_mode == "balanced":
        balance = np.divide(mn, mx, out=np.zeros_like(mx), where=mx > 0)
        return summed * (spec.alpha + (1.0 - spec.alpha) * np.power(balance, spec.gamma))
    raise ValueError(f"unknown addr_mode {spec.addr_mode}")


def time_signal(data: dict[str, np.ndarray], spec: FormulaSpec) -> np.ndarray:
    s_time = data["s_time"]
    s_type = data["s_type"]
    s_token = data["s_token"]
    if spec.time_mode == "raw":
        return s_time
    if spec.time_mode == "max_type_token":
        return s_time * np.maximum(s_type, s_token)
    if spec.time_mode == "avg_type_token":
        return s_time * ((s_type + s_token) / 2.0)
    if spec.time_mode == "token":
        return s_time * s_token
    if spec.time_mode == "type":
        return s_time * s_type
    raise ValueError(f"unknown time_mode {spec.time_mode}")


def formula_scores(data: dict[str, np.ndarray], spec: FormulaSpec, ablate: str = "") -> np.ndarray:
    addr = address_score(data, spec)
    weights = {
        "type": spec.w_type,
        "token": spec.w_token,
        "value": spec.w_value,
        "time": spec.w_time,
    }
    if ablate in {"context", "all_context"}:
        weights = {key: 0.0 for key in weights}
    elif ablate:
        weights[ablate] = 0.0
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        return addr
    ctx = (
        weights["type"] * data["s_type"]
        + weights["token"] * data["s_token"]
        + weights["value"] * data["s_value"]
        + weights["time"] * time_signal(data, spec)
    ) / weight_sum
    ctx = np.clip(ctx, 0.0, 1.0)
    return np.clip(addr * (spec.beta + (1.0 - spec.beta) * ctx), 0.0, 1.0)


def manual_additive_scores(data: dict[str, np.ndarray]) -> np.ndarray:
    return np.clip(
        0.30 * data["s_addr"]
        + 0.20 * data["s_type"]
        + 0.20 * data["s_token"]
        + 0.15 * data["s_time"]
        + 0.15 * data["s_value"],
        0.0,
        1.0,
    )


def build_specs() -> list[FormulaSpec]:
    addr_specs = [
        ("sum", 1.0, 1.0),
        ("balanced", 0.75, 1.0),
        ("balanced", 0.50, 1.0),
        ("balanced", 0.50, 2.0),
        ("harmonic", 1.0, 1.0),
        ("min", 1.0, 1.0),
    ]
    betas = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]
    time_modes = ["max_type_token", "avg_type_token", "token", "raw"]
    weight_sets = [
        (0.35, 0.35, 0.15, 0.15),
        (0.30, 0.30, 0.10, 0.30),
        (0.25, 0.25, 0.10, 0.40),
        (0.20, 0.20, 0.10, 0.50),
        (0.15, 0.15, 0.10, 0.60),
        (0.30, 0.45, 0.10, 0.15),
        (0.45, 0.30, 0.10, 0.15),
        (0.20, 0.55, 0.05, 0.20),
        (0.55, 0.20, 0.05, 0.20),
        (0.25, 0.35, 0.05, 0.35),
        (0.35, 0.25, 0.05, 0.35),
        (0.25, 0.25, 0.25, 0.25),
        (0.50, 0.50, 0.00, 0.00),
        (0.35, 0.65, 0.00, 0.00),
        (0.65, 0.35, 0.00, 0.00),
        (0.30, 0.50, 0.00, 0.20),
        (0.50, 0.30, 0.00, 0.20),
    ]
    specs: list[FormulaSpec] = []
    for addr_mode, alpha, gamma in addr_specs:
        for beta in betas:
            for time_mode in time_modes:
                for weights in weight_sets:
                    name = (
                        f"gate_{addr_mode}"
                        f"_a{alpha:.2f}_g{gamma:.1f}_b{beta:.2f}"
                        f"_t{time_mode}_w{weights[0]:.2f}-{weights[1]:.2f}-{weights[2]:.2f}-{weights[3]:.2f}"
                    )
                    specs.append(FormulaSpec(name, addr_mode, beta, *weights, time_mode, alpha, gamma))
    return specs


def row_for(name: str, metrics: dict[str, float | int], spec: FormulaSpec | None = None, ablation: str = "") -> dict[str, object]:
    row: dict[str, object] = {"name": name, "ablation": ablation}
    row.update(metrics)
    if spec is not None:
        row.update(asdict(spec))
    return row


def address_cache_key(spec: FormulaSpec) -> tuple[str, float, float]:
    return (spec.addr_mode, spec.alpha, spec.gamma)


def spec_from_csv_row(row: dict[str, str]) -> FormulaSpec:
    return FormulaSpec(
        name=row["name"],
        addr_mode=row["addr_mode"],
        beta=float(row["beta"]),
        w_type=float(row["w_type"]),
        w_token=float(row["w_token"]),
        w_value=float(row["w_value"]),
        w_time=float(row["w_time"]),
        time_mode=row["time_mode"],
        alpha=float(row.get("alpha", 1.0)),
        gamma=float(row.get("gamma", 1.0)),
    )


def csv_float(row: dict[str, str], key: str, default: float = 0.0) -> float:
    value = row.get(key, "")
    if value == "":
        return default
    return float(value)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as fobj:
        writer = csv.DictWriter(fobj, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def formula_text(spec: FormulaSpec) -> str:
    return (
        f"addr={spec.addr_mode}(alpha={spec.alpha:.2f}, gamma={spec.gamma:.2f}); "
        f"time={spec.time_mode}; "
        f"context=({spec.w_type:.3f}*S_type + {spec.w_token:.3f}*S_token + "
        f"{spec.w_value:.3f}*S_value + {spec.w_time:.3f}*R_time)"
        f"/{spec.w_type + spec.w_token + spec.w_value + spec.w_time:.3f}; "
        f"risk=addr*({spec.beta:.3f} + {1.0 - spec.beta:.3f}*context)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Search constrained risk-score formulas.")
    parser.add_argument("--feature-dir", default="results/colab_risk_training_full_20260614/features")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--split-column", choices=["split_time", "split_victim"], default="split_time")
    parser.add_argument("--batch-size", type=int, default=250_000)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--grid-size", type=int, default=1001)
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--candidate-csv", default="", help="reuse a previous formula_full_validation.csv and only run detailed ablations")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path("results") / f"risk_formula_search_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = iter_feature_files(Path(args.feature_dir), args.max_files)
    thresholds = np.linspace(0.0, 1.0, args.grid_size, dtype=np.float64)
    print(f"[load] files={len(files)} split_column={args.split_column}", flush=True)
    val = load_split(files, args.split_column, "val", args.batch_size)
    test = load_split(files, args.split_column, "test", args.batch_size)
    print(f"[load] val_rows={val['y'].size:,} test_rows={test['y'].size:,}", flush=True)

    baseline_specs = {
        "address_only_sum": val["s_addr"],
        "prefix_only": prefix_score(val),
        "suffix_only": suffix_score(val),
        "manual_additive": manual_additive_scores(val),
    }
    baseline_val = {name: metrics_at_best_tau(scores, val["y"], thresholds) for name, scores in baseline_specs.items()}
    baseline_ceiling = max(float(item["f1"]) for item in baseline_val.values())
    print("[baseline] validation", json.dumps(baseline_val, indent=2), flush=True)

    val_rows: list[dict[str, object]] = []
    same_address_cache: dict[tuple[str, float, float], dict[str, float | int]] = {}
    if args.candidate_csv:
        with Path(args.candidate_csv).open(newline="", encoding="utf-8") as fobj:
            candidate_rows = list(csv.DictReader(fobj))
        if not candidate_rows:
            raise RuntimeError(f"candidate csv is empty: {args.candidate_csv}")
        ranked_rows = sorted(
            candidate_rows,
            key=lambda row: (
                csv_float(row, "margin_vs_fair_ceiling"),
                csv_float(row, "margin_vs_same_address"),
                csv_float(row, "f1"),
                csv_float(row, "precision"),
                csv_float(row, "recall"),
            ),
            reverse=True,
        )
        top_specs = [spec_from_csv_row(row) for row in ranked_rows[: args.top_k]]
        write_csv(out_dir / "formula_full_validation.csv", [dict(row) for row in candidate_rows])
        print(f"[resume] candidates={len(candidate_rows)} detailed={len(top_specs)}", flush=True)
    else:
        specs = build_specs()
        for i, spec in enumerate(specs, start=1):
            metrics = metrics_at_best_tau(formula_scores(val, spec), val["y"], thresholds)
            key = address_cache_key(spec)
            if key not in same_address_cache:
                same_address_cache[key] = metrics_at_best_tau(address_score(val, spec), val["y"], thresholds)
            same_address = same_address_cache[key]
            fair_ceiling = max(baseline_ceiling, float(same_address["f1"]))
            row = row_for(spec.name, metrics, spec)
            row["margin_vs_baseline"] = float(metrics["f1"]) - baseline_ceiling
            row["same_address_f1"] = float(same_address["f1"])
            row["margin_vs_same_address"] = float(metrics["f1"]) - float(same_address["f1"])
            row["margin_vs_fair_ceiling"] = float(metrics["f1"]) - fair_ceiling
            val_rows.append(row)
            if i % 100 == 0 or i == len(specs):
                best = max(
                    val_rows,
                    key=lambda item: (
                        float(item["margin_vs_fair_ceiling"]),
                        float(item["f1"]),
                        float(item["precision"]),
                        float(item["recall"]),
                    ),
                )
                print(
                    f"[search] {i}/{len(specs)} best={best['name']} "
                    f"val_f1={float(best['f1']):.9f} "
                    f"fair_margin={float(best['margin_vs_fair_ceiling']):.9f}",
                    flush=True,
                )
        write_csv(out_dir / "formula_full_validation.csv", val_rows)

        top_specs = sorted(
            specs,
            key=lambda spec: (
                float(next(row for row in val_rows if row["name"] == spec.name)["margin_vs_fair_ceiling"]),
                float(next(row for row in val_rows if row["name"] == spec.name)["margin_vs_same_address"]),
                float(next(row for row in val_rows if row["name"] == spec.name)["f1"]),
            ),
            reverse=True,
        )[: args.top_k]

    ablation_rows: list[dict[str, object]] = []
    for spec in top_specs:
        key = address_cache_key(spec)
        if key not in same_address_cache:
            same_address_cache[key] = metrics_at_best_tau(address_score(val, spec), val["y"], thresholds)
        full = metrics_at_best_tau(formula_scores(val, spec), val["y"], thresholds)
        candidates = [("full", full)]
        candidates.append(("address_only_same", same_address_cache[address_cache_key(spec)]))
        for ablation in ("type", "token", "value", "time"):
            candidates.append((f"no_{ablation}", metrics_at_best_tau(formula_scores(val, spec, ablation), val["y"], thresholds)))
        candidates.extend((name, metrics) for name, metrics in baseline_val.items())
        best_ablation = max(float(metrics["f1"]) for name, metrics in candidates if name != "full")
        for name, metrics in candidates:
            row = row_for(spec.name, metrics, spec, name)
            row["full_margin_vs_best_ablation"] = float(full["f1"]) - best_ablation
            ablation_rows.append(row)
    write_csv(out_dir / "formula_ablation_validation.csv", ablation_rows)

    ranked_specs = []
    for spec in top_specs:
        full_rows = [row for row in ablation_rows if row["name"] == spec.name and row["ablation"] == "full"]
        ranked_specs.append((float(full_rows[0]["full_margin_vs_best_ablation"]), float(full_rows[0]["f1"]), spec))
    ranked_specs.sort(reverse=True, key=lambda item: (item[0], item[1]))

    test_rows: list[dict[str, object]] = []
    test_baselines = {
        "address_only_sum": metrics_at_best_tau(test["s_addr"], test["y"], thresholds),
        "prefix_only": metrics_at_best_tau(prefix_score(test), test["y"], thresholds),
        "suffix_only": metrics_at_best_tau(suffix_score(test), test["y"], thresholds),
        "manual_additive": metrics_at_best_tau(manual_additive_scores(test), test["y"], thresholds),
    }
    for _, _, spec in ranked_specs[:10]:
        full = metrics_at_best_tau(formula_scores(test, spec), test["y"], thresholds)
        candidates = [("full", full)]
        candidates.append(("address_only_same", metrics_at_best_tau(formula_scores(test, spec, "context"), test["y"], thresholds)))
        for ablation in ("type", "token", "value", "time"):
            candidates.append((f"no_{ablation}", metrics_at_best_tau(formula_scores(test, spec, ablation), test["y"], thresholds)))
        candidates.extend(test_baselines.items())
        best_ablation = max(float(metrics["f1"]) for name, metrics in candidates if name != "full")
        for name, metrics in candidates:
            row = row_for(spec.name, metrics, spec, name)
            row["full_margin_vs_best_ablation"] = float(full["f1"]) - best_ablation
            test_rows.append(row)
    write_csv(out_dir / "formula_ablation_test.csv", test_rows)

    best_spec = ranked_specs[0][2]
    best_payload = {
        "feature_dir": str(args.feature_dir),
        "split_column": args.split_column,
        "threshold_grid_size": args.grid_size,
        "validation_rows": int(val["y"].size),
        "test_rows": int(test["y"].size),
        "selected_by": "max validation margin over own ablations, same-address-only, and address/prefix/suffix/manual baselines",
        "best_spec": asdict(best_spec),
        "formula": formula_text(best_spec),
        "validation": [row for row in ablation_rows if row["name"] == best_spec.name],
        "test": [row for row in test_rows if row["name"] == best_spec.name],
    }
    (out_dir / "best_formula.json").write_text(json.dumps(best_payload, indent=2), encoding="utf-8")
    print("[best]", formula_text(best_spec), flush=True)
    print(f"[done] wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
