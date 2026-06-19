#!/usr/bin/env python3
"""Export best-candidate risk-score features for Colab training.

The exporter consumes the full-label replay shards produced by
``python/benchmark_pipeline.py --full-label-replay`` and writes one Parquet
feature shard per input shard. Each output row represents the best active
candidate found for one replay event at a canonical observation delay.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_WEIGHTS = (0.30, 0.20, 0.20, 0.15, 0.15)
DEFAULT_SCORE_MODE = "context_gate"
DEFAULT_ADDRESS_SCORE_MODE = "sum"
DEFAULT_ADDRESS_BALANCE_ALPHA = 0.50
DEFAULT_ADDRESS_BALANCE_GAMMA = 1.0
DEFAULT_CONTEXT_GATE_BASE = 0.30
DEFAULT_CONTEXT_WEIGHTS = (0.65, 0.35, 0.0, 0.0)
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


@dataclass(frozen=True)
class ExportConfig:
    kp: int
    ks: int
    min_prefix_depth: int
    min_suffix_depth: int
    max_candidates_per_side: int
    window_days: int
    lambda_seconds: float
    tiny_value: float
    weights: tuple[float, float, float, float, float]
    score_mode: str
    address_score_mode: str
    address_balance_alpha: float
    address_balance_gamma: float
    context_gate_base: float
    context_weights: tuple[float, float, float, float]
    delay_seconds: int


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_time(value: str) -> dt.datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def normalize_address(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 40:
        return ""
    for ch in text:
        if ch not in "0123456789abcdef":
            return ""
    return text


def normalize_token(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text


def same_non_empty(left: str, right: str) -> bool:
    left = str(left or "").strip().lower()
    right = str(right or "").strip().lower()
    return bool(left and right and left == right)


def prefix_match(left: str, right: str) -> int:
    n = min(len(left), len(right))
    for idx in range(n):
        if left[idx] != right[idx]:
            return idx
    return n


def suffix_match(left: str, right: str) -> int:
    n = min(len(left), len(right))
    for idx in range(1, n + 1):
        if left[-idx] != right[-idx]:
            return idx - 1
    return n


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path, hash_file: bool) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    record: dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
    }
    if hash_file:
        record["sha256"] = sha256_file(path)
    return record


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_token_metadata(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    if isinstance(payload, dict):
        values = payload.values()
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    for item in values:
        if not isinstance(item, dict):
            continue
        key = normalize_token(item.get("address", ""))
        if key:
            out[key] = {
                "symbol": item.get("symbol", ""),
                "name": item.get("name", ""),
                "metadata_missing": bool(item.get("metadata_missing", False)),
            }
    return out


def token_context_score(metadata: dict[str, dict[str, Any]], pending_token: str, cp: dict[str, Any]) -> float:
    pending_key = normalize_token(pending_token)
    trusted_key = normalize_token(cp.get("token", ""))
    if not pending_key or not trusted_key:
        return 0.4
    if pending_key == trusted_key:
        return 0.2

    pending_md = metadata.get(pending_key, {})
    cp_md = {
        "symbol": cp.get("token_symbol", ""),
        "name": cp.get("token_name", ""),
        "metadata_missing": bool(cp.get("metadata_missing", False)),
    }
    if not cp_md["symbol"] and not cp_md["name"]:
        cp_md = metadata.get(trusted_key, cp_md)

    if pending_md and not pending_md.get("metadata_missing", False) and not cp_md.get("metadata_missing", False):
        if same_non_empty(pending_md.get("symbol", ""), cp_md.get("symbol", "")):
            return 1.0
        if same_non_empty(pending_md.get("name", ""), cp_md.get("name", "")):
            return 1.0
        return 0.7
    return 0.7


def transfer_type_score(value: float, tiny_value: float) -> float:
    if value == 0:
        return 1.0
    if 0 < value <= tiny_value:
        return 0.8
    return 0.25


def value_risk_score(value: float, tiny_value: float) -> float:
    if value == 0:
        return 1.0
    if value <= tiny_value:
        return 0.7
    score = 1.0 / (1.0 + math.log10(1.0 + max(0.0, value)))
    return max(0.1, score)


def is_active(observed_at: dt.datetime, last_seen: dt.datetime, window_days: int) -> bool:
    if last_seen > observed_at:
        return False
    if window_days <= 0:
        return True
    return observed_at - last_seen <= dt.timedelta(days=window_days)


class VictimIndex:
    def __init__(self, cfg: ExportConfig, metadata: dict[str, dict[str, Any]]) -> None:
        self.cfg = cfg
        self.metadata = metadata
        self.histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.prefix_postings: dict[str, Counter[str]] = defaultdict(Counter)
        self.suffix_postings: dict[str, Counter[str]] = defaultdict(Counter)

    def add_counterparty(self, row: dict[str, Any]) -> None:
        recipient = normalize_address(row.get("recipient", ""))
        if not recipient:
            return
        cp = dict(row)
        cp["recipient_norm"] = recipient
        cp["token_norm"] = normalize_token(cp.get("token", ""))
        cp["last_seen_dt"] = parse_time(str(cp.get("last_seen", "")))
        cp["observed_freq_int"] = int(cp.get("observed_freq", 0) or 0)
        self.histories[recipient].append(cp)
        prefix_key = recipient[: self.cfg.kp]
        suffix_key = recipient[-self.cfg.ks :][::-1]
        for depth in range(1, len(prefix_key) + 1):
            self.prefix_postings[prefix_key[:depth]][recipient] += 1
        for depth in range(1, len(suffix_key) + 1):
            self.suffix_postings[suffix_key[:depth]][recipient] += 1

    def candidate_ids(self, event: dict[str, Any], observed_at: dt.datetime, lookalike: str) -> dict[str, int]:
        pref_key = lookalike[: self.cfg.kp]
        suff_key = lookalike[-self.cfg.ks :][::-1]
        pref: Counter[str] = Counter()
        suff: Counter[str] = Counter()
        for depth in range(self.cfg.min_prefix_depth, min(len(pref_key), self.cfg.kp) + 1):
            pref.update(self.prefix_postings.get(pref_key[:depth], {}))
        for depth in range(self.cfg.min_suffix_depth, min(len(suff_key), self.cfg.ks) + 1):
            suff.update(self.suffix_postings.get(suff_key[:depth], {}))
        pref = self.prune(event, observed_at, pref)
        suff = self.prune(event, observed_at, suff)
        out: dict[str, int] = {}
        for recipient, freq in pref.items():
            out[recipient] = out.get(recipient, 0) + int(freq)
        for recipient, freq in suff.items():
            out[recipient] = out.get(recipient, 0) + int(freq)
        return out

    def prune(self, event: dict[str, Any], observed_at: dt.datetime, candidates: Counter[str]) -> Counter[str]:
        limit = self.cfg.max_candidates_per_side
        if limit <= 0 or len(candidates) <= limit:
            return candidates
        pending_token = event.get("token_address", "")
        ranked: list[tuple[float, int, str]] = []
        for recipient, trie_freq in candidates.items():
            histories = self.histories.get(recipient, [])
            rank_score = float(trie_freq)
            freq = int(trie_freq)
            for cp in histories:
                if not is_active(observed_at, cp["last_seen_dt"], self.cfg.window_days):
                    continue
                freq = max(freq, int(cp.get("observed_freq_int", 0)))
                if pending_token and normalize_token(pending_token) == cp.get("token_norm", ""):
                    rank_score += 1_000_000
                elif token_context_score(self.metadata, pending_token, cp) >= 1.0:
                    rank_score += 500_000
                days = max(0.0, (observed_at - cp["last_seen_dt"]).total_seconds() / 86400.0)
                rank_score += 10_000 / (1.0 + days)
            ranked.append((rank_score, freq, recipient))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return Counter({recipient: candidates[recipient] for _, _, recipient in ranked[:limit]})

    def best_features(self, event: dict[str, Any], observed_at: dt.datetime, lookalike: str) -> dict[str, Any]:
        candidates = self.candidate_ids(event, observed_at, lookalike)
        best: dict[str, Any] | None = None
        best_total = -1.0
        best_last_seen = dt.datetime.fromtimestamp(0, dt.timezone.utc)
        total_active_scored = 0
        for recipient in candidates:
            if recipient == lookalike:
                continue
            histories = self.histories.get(recipient, [])
            ap = prefix_match(lookalike, recipient)
            sp = suffix_match(lookalike, recipient)
            for cp in histories:
                if not is_active(observed_at, cp["last_seen_dt"], self.cfg.window_days):
                    continue
                total_active_scored += 1
                features = score_components(self.cfg, self.metadata, event, cp, lookalike, recipient, ap, sp, observed_at)
                total = score_total(self.cfg, features)
                last_seen = cp["last_seen_dt"]
                if total > best_total or (total == best_total and last_seen > best_last_seen):
                    best_total = total
                    best_last_seen = last_seen
                    best = features
        if best is None:
            best = empty_feature_values()
        best["candidates_scored"] = total_active_scored
        best["found_candidate"] = 1 if best_total >= 0 else 0
        add_interactions(best)
        return best


def score_components(
    cfg: ExportConfig,
    metadata: dict[str, dict[str, Any]],
    event: dict[str, Any],
    cp: dict[str, Any],
    lookalike: str,
    recipient: str,
    matched_prefix: int,
    matched_suffix: int,
    observed_at: dt.datetime,
) -> dict[str, Any]:
    value = float(event.get("value_normalized", event.get("value", 0.0)) or 0.0)
    delta = max(0.0, (observed_at - cp["last_seen_dt"]).total_seconds())
    s_time = math.exp(-delta / cfg.lambda_seconds) if cfg.lambda_seconds > 0 else 0.0
    s_addr = (min(matched_prefix, cfg.kp) + min(matched_suffix, cfg.ks)) / float(cfg.kp + cfg.ks)
    return {
        "matched_recipient": "0x" + recipient,
        "matched_prefix": matched_prefix,
        "matched_suffix": matched_suffix,
        "s_addr": s_addr,
        "s_type": transfer_type_score(value, cfg.tiny_value),
        "s_token": token_context_score(metadata, str(event.get("token_address", "")), cp),
        "s_time": s_time,
        "s_value": value_risk_score(value, cfg.tiny_value),
    }


def empty_feature_values() -> dict[str, Any]:
    return {
        "matched_recipient": "",
        "matched_prefix": 0,
        "matched_suffix": 0,
        "s_addr": 0.0,
        "s_type": 0.0,
        "s_token": 0.0,
        "s_time": 0.0,
        "s_value": 0.0,
    }


def score_total(cfg: ExportConfig, row: dict[str, Any]) -> float:
    s_addr = address_evidence_score(cfg, row)
    s_type = float(row["s_type"])
    s_token = float(row["s_token"])
    s_time = float(row["s_time"])
    s_value = float(row["s_value"])
    if cfg.score_mode.strip().lower() in {"context_gate", "context_gated_temporal"}:
        conditioned_time = s_time * max(s_type, s_token)
        weights = cfg.context_weights
        weight_sum = sum(weights)
        if weight_sum <= 0:
            weights = DEFAULT_CONTEXT_WEIGHTS
            weight_sum = sum(weights)
        context = (
            weights[0] * s_type
            + weights[1] * s_token
            + weights[2] * s_value
            + weights[3] * conditioned_time
        ) / weight_sum
        context = min(1.0, max(0.0, context))
        base = cfg.context_gate_base if 0 <= cfg.context_gate_base < 1 else DEFAULT_CONTEXT_GATE_BASE
        return s_addr * (base + (1.0 - base) * context)
    weights = cfg.weights
    return weights[0] * s_addr + weights[1] * s_type + weights[2] * s_token + weights[3] * s_time + weights[4] * s_value


def address_evidence_score(cfg: ExportConfig, row: dict[str, Any]) -> float:
    raw = float(row["s_addr"])
    mode = cfg.address_score_mode.strip().lower()
    if mode not in {"balanced", "balanced_sum", "balance"}:
        return raw
    prefix = min(int(row["matched_prefix"]), cfg.kp) / float(cfg.kp)
    suffix = min(int(row["matched_suffix"]), cfg.ks) / float(cfg.ks)
    mx = max(prefix, suffix)
    if mx <= 0:
        return 0.0
    balance = min(prefix, suffix) / mx
    alpha = cfg.address_balance_alpha if 0 <= cfg.address_balance_alpha <= 1 else DEFAULT_ADDRESS_BALANCE_ALPHA
    gamma = cfg.address_balance_gamma if cfg.address_balance_gamma > 0 else DEFAULT_ADDRESS_BALANCE_GAMMA
    return raw * (alpha + (1.0 - alpha) * (balance**gamma))


def add_interactions(row: dict[str, Any]) -> None:
    s_addr = float(row["s_addr"])
    row["s_addr_x_type"] = s_addr * float(row["s_type"])
    row["s_addr_x_token"] = s_addr * float(row["s_token"])
    row["s_addr_x_time"] = s_addr * float(row["s_time"])
    row["s_addr_x_value"] = s_addr * float(row["s_value"])
    matched_prefix = int(row["matched_prefix"])
    matched_suffix = int(row["matched_suffix"])
    row["address_gate_weak"] = 1 if (matched_prefix >= 3 or matched_suffix >= 3) else 0
    row["address_gate_strong"] = 1 if (matched_prefix >= 4 and matched_suffix >= 4) else 0


def split_victim(victim: str) -> str:
    digest = hashlib.sha256(victim.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "val"
    return "test"


def split_time(day: str, cutoffs: dict[str, str]) -> str:
    if day <= cutoffs["train_end_day"]:
        return "train"
    if day <= cutoffs["val_end_day"]:
        return "val"
    return "test"


def count_shard_days(events_path: Path) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    rows = 0
    for event in iter_jsonl(events_path):
        block_time = parse_time(str(event["block_time"]))
        day = block_time.date().isoformat()
        counts[day] += 1
        labels["positive" if bool(event.get("is_poisoning", False)) else "negative"] += 1
        rows += 1
    return {"path": str(events_path), "rows": rows, "days": dict(counts), "labels": dict(labels)}


def choose_time_cutoffs(day_counts: Counter[str]) -> dict[str, str]:
    total = sum(day_counts.values())
    if total <= 0:
        raise ValueError("cannot choose time splits from empty day counts")
    train_target = total * 0.70
    val_target = total * 0.85
    cumulative = 0
    train_end = ""
    val_end = ""
    for day in sorted(day_counts):
        cumulative += day_counts[day]
        if not train_end and cumulative >= train_target:
            train_end = day
        if not val_end and cumulative >= val_target:
            val_end = day
            break
    return {
        "strategy": "event_count_chronological_70_15_15",
        "train_end_day": train_end,
        "val_end_day": val_end,
    }


def build_index(counterparties_path: Path, cfg: ExportConfig, metadata: dict[str, dict[str, Any]]) -> dict[str, VictimIndex]:
    victims: dict[str, VictimIndex] = {}
    for cp in iter_jsonl(counterparties_path):
        victim = normalize_address(cp.get("victim", ""))
        if not victim:
            continue
        idx = victims.get(victim)
        if idx is None:
            idx = VictimIndex(cfg, metadata)
            victims[victim] = idx
        idx.add_counterparty(cp)
    return victims


def export_shard(job: dict[str, Any]) -> dict[str, Any]:
    cfg = ExportConfig(**job["cfg"])
    metadata = load_token_metadata(Path(job["token_metadata_path"])) if job["token_metadata_path"] else {}
    shard_id = int(job["shard_id"])
    events_path = Path(job["events_path"])
    counterparties_path = Path(job["counterparties_path"])
    out_path = Path(job["out_path"])
    cutoffs = job["time_cutoffs"]

    victims = build_index(counterparties_path, cfg, metadata)
    columns: dict[str, list[Any]] = defaultdict(list)
    positive = 0
    negative = 0
    found = 0
    rows = 0
    for event in iter_jsonl(events_path):
        block_time = parse_time(str(event["block_time"]))
        observed_at = block_time - dt.timedelta(seconds=cfg.delay_seconds)
        day = block_time.date().isoformat()
        victim_norm = normalize_address(event.get("victim", ""))
        lookalike_norm = normalize_address(event.get("lookalike", ""))
        features = empty_feature_values()
        features["candidates_scored"] = 0
        features["found_candidate"] = 0
        add_interactions(features)
        if victim_norm and lookalike_norm and victim_norm in victims:
            features = victims[victim_norm].best_features(event, observed_at, lookalike_norm)

        label = 1 if bool(event.get("is_poisoning", False)) else 0
        positive += label
        negative += 1 - label
        found += int(features["found_candidate"])
        rows += 1

        base = {
            "tx_hash": str(event.get("source_tx_hash", event.get("hash", ""))),
            "event_id": str(event.get("hash", "")),
            "block_number": int(event.get("block_number", 0) or 0),
            "day": day,
            "shard": shard_id,
            "victim": "0x" + victim_norm if victim_norm else "",
            "lookalike": "0x" + lookalike_norm if lookalike_norm else "",
            "matched_recipient": features.get("matched_recipient", ""),
            "label": label,
            "split_time": split_time(day, cutoffs),
            "split_victim": split_victim(victim_norm),
        }
        for key, value in base.items():
            columns[key].append(value)
        for key in FEATURE_COLUMNS:
            columns[key].append(features.get(key, 0))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict(dict(columns), schema=feature_schema())
    pq.write_table(table, out_path, compression="zstd", use_dictionary=True)
    return {
        "shard": shard_id,
        "rows": rows,
        "positives": positive,
        "negatives": negative,
        "found_candidate": found,
        "path": str(out_path),
        "size_bytes": out_path.stat().st_size,
    }


def feature_schema() -> pa.Schema:
    return pa.schema(
        [
            ("tx_hash", pa.string()),
            ("event_id", pa.string()),
            ("block_number", pa.int64()),
            ("day", pa.string()),
            ("shard", pa.int16()),
            ("victim", pa.string()),
            ("lookalike", pa.string()),
            ("matched_recipient", pa.string()),
            ("label", pa.int8()),
            ("s_addr", pa.float32()),
            ("s_type", pa.float32()),
            ("s_token", pa.float32()),
            ("s_time", pa.float32()),
            ("s_value", pa.float32()),
            ("matched_prefix", pa.int16()),
            ("matched_suffix", pa.int16()),
            ("candidates_scored", pa.int32()),
            ("found_candidate", pa.int8()),
            ("s_addr_x_type", pa.float32()),
            ("s_addr_x_token", pa.float32()),
            ("s_addr_x_time", pa.float32()),
            ("s_addr_x_value", pa.float32()),
            ("address_gate_weak", pa.int8()),
            ("address_gate_strong", pa.int8()),
            ("split_time", pa.string()),
            ("split_victim", pa.string()),
        ]
    )


def schema_description() -> dict[str, Any]:
    return {
        "row_unit": "best active retrieved candidate per canonical replay event",
        "feature_columns": FEATURE_COLUMNS,
        "labels": {
            "1": "zero_value_transfer OR tiny_transfer OR counterfeit_token_transfer",
            "0": "valid intended-transfer negative after excluding poisoning/payoff rows",
        },
        "splits": {
            "split_time": "chronological event-count 70/15/15 split",
            "split_victim": "stable sha256(victim) 70/15/15 split",
        },
    }


def enrich_block_numbers(out_dir: Path, dataset_cache: Path, shard_summaries: list[dict[str, Any]], threads: int) -> int:
    try:
        import duckdb
    except Exception as exc:  # pragma: no cover - depends on local export env
        raise RuntimeError("duckdb is required to enrich block_number from --dataset-cache") from exc

    if not dataset_cache.exists():
        raise FileNotFoundError(f"--dataset-cache not found for block_number enrichment: {dataset_cache}")

    db_path = out_dir / "_block_number_lookup.duckdb"
    if db_path.exists():
        db_path.unlink()

    def sql_string(value: Path | str) -> str:
        text = str(value).replace("\\", "/").replace("'", "''")
        return f"'{text}'"

    feature_columns = [field.name for field in feature_schema()]
    select_parts = []
    for name in feature_columns:
        if name == "block_number":
            select_parts.append("CAST(COALESCE(b.block_number, f.block_number, 0) AS BIGINT) AS block_number")
        else:
            select_parts.append(f"f.{name}")
    select_sql = ",\n                ".join(select_parts)

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA threads={max(1, threads)}")
        conn.execute(
            """
            CREATE TABLE block_numbers AS
            SELECT tx_hash, max(block_number)::BIGINT AS block_number
            FROM read_parquet(?)
            GROUP BY tx_hash
            """,
            [str(dataset_cache)],
        )
        conn.execute("CREATE INDEX block_numbers_tx_hash_idx ON block_numbers(tx_hash)")

        missing_total = 0
        for summary in shard_summaries:
            path = Path(str(summary["path"]))
            tmp_path = path.with_suffix(".tmp.parquet")
            if tmp_path.exists():
                tmp_path.unlink()
            conn.execute(
                f"""
                COPY (
                    SELECT
                        {select_sql}
                    FROM read_parquet({sql_string(path)}) AS f
                    LEFT JOIN block_numbers AS b
                    USING (tx_hash)
                )
                TO {sql_string(tmp_path)} (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )
            tmp_path.replace(path)
            missing = conn.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE block_number IS NULL OR block_number = 0",
                [str(path)],
            ).fetchone()[0]
            summary["missing_block_numbers"] = int(missing)
            summary["size_bytes"] = path.stat().st_size
            missing_total += int(missing)
        return missing_total
    finally:
        conn.close()
        if db_path.exists():
            db_path.unlink()
        wal_path = Path(str(db_path) + ".wal")
        if wal_path.exists():
            wal_path.unlink()


def resolve_shard_jobs(args: argparse.Namespace, cfg_dict: dict[str, Any], cutoffs: dict[str, str]) -> list[dict[str, Any]]:
    shards_dir = Path(args.shards_dir)
    events_dir = shards_dir / "events"
    counterparties_dir = shards_dir / "counterparties"
    shard_ids = [int(x.stem.split("_")[-1]) for x in events_dir.glob("shard_*.jsonl")]
    shard_ids.sort()
    if args.shards:
        requested: list[int] = []
        for part in args.shards.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                requested.extend(range(int(start), int(end) + 1))
            else:
                requested.append(int(part))
        shard_ids = [sid for sid in shard_ids if sid in set(requested)]
    if args.max_shards > 0:
        shard_ids = shard_ids[: args.max_shards]

    out_dir = Path(args.out_dir)
    jobs = []
    for shard_id in shard_ids:
        events_path = events_dir / f"shard_{shard_id:04d}.jsonl"
        counterparties_path = counterparties_dir / f"shard_{shard_id:04d}.jsonl"
        if not events_path.exists() or not counterparties_path.exists():
            continue
        jobs.append(
            {
                "shard_id": shard_id,
                "events_path": str(events_path),
                "counterparties_path": str(counterparties_path),
                "out_path": str(out_dir / "features" / f"part-{shard_id:04d}.parquet"),
                "token_metadata_path": str(args.token_metadata) if args.token_metadata else "",
                "cfg": cfg_dict,
                "time_cutoffs": cutoffs,
            }
        )
    return jobs


def parse_weights(raw: str) -> tuple[float, float, float, float, float]:
    parts = [float(x.strip()) for x in raw.split(",") if x.strip()]
    if len(parts) != 5:
        raise ValueError("--weights must contain exactly 5 comma-separated values")
    return tuple(parts)  # type: ignore[return-value]


def parse_context_weights(raw: str) -> tuple[float, float, float, float]:
    parts = [float(x.strip()) for x in raw.split(",") if x.strip()]
    if len(parts) != 4:
        raise ValueError("--context-weights must contain exactly 4 comma-separated values")
    return tuple(parts)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Colab-ready risk-score training features.")
    parser.add_argument("--shards-dir", default="results/full_label_full_dataset_20260514_tau040/full_label_shards")
    parser.add_argument("--source-manifest", default="results/full_label_full_dataset_20260514_tau040/full_label_manifest.json")
    parser.add_argument("--dataset-cache", default="data/normalized/address_poisoning_ethereum.normalized.full.parquet")
    parser.add_argument("--token-metadata", default="results/rpc_cache/full_dataset_token_metadata_cache.json")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--delay-seconds", type=int, default=15)
    parser.add_argument("--kp", type=int, default=6)
    parser.add_argument("--ks", type=int, default=6)
    parser.add_argument("--min-prefix-depth", type=int, default=3)
    parser.add_argument("--min-suffix-depth", type=int, default=3)
    parser.add_argument("--max-candidates-per-side", type=int, default=2048)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--lambda-seconds", type=float, default=604800.0)
    parser.add_argument("--tiny-value", type=float, default=10.0)
    parser.add_argument("--weights", default=",".join(str(x) for x in DEFAULT_WEIGHTS))
    parser.add_argument("--score-mode", default=DEFAULT_SCORE_MODE)
    parser.add_argument("--address-score-mode", default=DEFAULT_ADDRESS_SCORE_MODE)
    parser.add_argument("--address-balance-alpha", type=float, default=DEFAULT_ADDRESS_BALANCE_ALPHA)
    parser.add_argument("--address-balance-gamma", type=float, default=DEFAULT_ADDRESS_BALANCE_GAMMA)
    parser.add_argument("--context-gate-base", type=float, default=DEFAULT_CONTEXT_GATE_BASE)
    parser.add_argument("--context-weights", default=",".join(str(x) for x in DEFAULT_CONTEXT_WEIGHTS))
    parser.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--shards", default="", help="Comma-separated shard ids or ranges, e.g. 0,2,10-15.")
    parser.add_argument("--max-shards", type=int, default=0, help="Smoke-test limit after shard selection.")
    parser.add_argument("--hash-large-sources", action="store_true")
    parser.add_argument("--skip-block-number-enrich", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path("results") / f"colab_risk_training_full_{dt.datetime.now().strftime('%Y%m%d')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = ExportConfig(
        kp=args.kp,
        ks=args.ks,
        min_prefix_depth=args.min_prefix_depth,
        min_suffix_depth=args.min_suffix_depth,
        max_candidates_per_side=args.max_candidates_per_side,
        window_days=args.window_days,
        lambda_seconds=args.lambda_seconds,
        tiny_value=args.tiny_value,
        weights=parse_weights(args.weights),
        score_mode=args.score_mode,
        address_score_mode=args.address_score_mode,
        address_balance_alpha=args.address_balance_alpha,
        address_balance_gamma=args.address_balance_gamma,
        context_gate_base=args.context_gate_base,
        context_weights=parse_context_weights(args.context_weights),
        delay_seconds=args.delay_seconds,
    )
    cfg_dict = {
        "kp": cfg.kp,
        "ks": cfg.ks,
        "min_prefix_depth": cfg.min_prefix_depth,
        "min_suffix_depth": cfg.min_suffix_depth,
        "max_candidates_per_side": cfg.max_candidates_per_side,
        "window_days": cfg.window_days,
        "lambda_seconds": cfg.lambda_seconds,
        "tiny_value": cfg.tiny_value,
        "weights": cfg.weights,
        "score_mode": cfg.score_mode,
        "address_score_mode": cfg.address_score_mode,
        "address_balance_alpha": cfg.address_balance_alpha,
        "address_balance_gamma": cfg.address_balance_gamma,
        "context_gate_base": cfg.context_gate_base,
        "context_weights": cfg.context_weights,
        "delay_seconds": cfg.delay_seconds,
    }

    events_dir = Path(args.shards_dir) / "events"
    event_paths = sorted(events_dir.glob("shard_*.jsonl"))
    if args.shards or args.max_shards > 0:
        jobs_for_selection = resolve_shard_jobs(args, cfg_dict, {"train_end_day": "9999-12-31", "val_end_day": "9999-12-31"})
        selected_ids = {int(job["shard_id"]) for job in jobs_for_selection}
        event_paths = [path for path in event_paths if int(path.stem.split("_")[-1]) in selected_ids]
    if not event_paths:
        raise FileNotFoundError(f"no shard event files found under {events_dir}")

    day_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    total_events = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for idx, result in enumerate(pool.map(count_shard_days, event_paths), 1):
            day_counts.update(result["days"])
            label_counts.update(result["labels"])
            total_events += int(result["rows"])
            print(f"[split {idx}/{len(event_paths)}] counted {result['rows']} rows from {Path(result['path']).name}", flush=True)

    cutoffs = choose_time_cutoffs(day_counts)
    jobs = resolve_shard_jobs(args, cfg_dict, cutoffs)
    shard_summaries: list[dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(export_shard, job): job for job in jobs}
        for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            shard_summaries.append(result)
            print(f"[export {idx}/{len(jobs)}] shard={result['shard']:04d} rows={result['rows']:,}", flush=True)

    shard_summaries.sort(key=lambda row: int(row["shard"]))
    missing_block_numbers = 0
    if not args.skip_block_number_enrich:
        print("[block-number] enriching feature shards from dataset cache", flush=True)
        missing_block_numbers = enrich_block_numbers(
            out_dir=out_dir,
            dataset_cache=Path(args.dataset_cache),
            shard_summaries=shard_summaries,
            threads=args.jobs,
        )
        print(f"[block-number] missing after enrichment: {missing_block_numbers:,}", flush=True)
    exported_rows = sum(int(row["rows"]) for row in shard_summaries)
    positives = sum(int(row["positives"]) for row in shard_summaries)
    negatives = sum(int(row["negatives"]) for row in shard_summaries)
    found = sum(int(row["found_candidate"]) for row in shard_summaries)

    source_manifest_path = Path(args.source_manifest)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8")) if source_manifest_path.exists() else {}
    manifest = {
        "created_at": utc_now_iso(),
        "status": "complete",
        "row_unit": "best_candidate_per_event",
        "delay_seconds": args.delay_seconds,
        "loss_rate": 0.0,
        "source_shards_dir": str(Path(args.shards_dir)),
        "source_manifest": str(source_manifest_path),
        "dataset_cache": str(Path(args.dataset_cache)),
        "feature_dir": str(out_dir / "features"),
        "shards_exported": len(shard_summaries),
        "rows": exported_rows,
        "positives": positives,
        "negatives": negatives,
        "found_candidate_rows": found,
        "block_numbers_enriched": not args.skip_block_number_enrich,
        "block_numbers_missing": missing_block_numbers,
        "source_event_rows": source_manifest.get("event_rows"),
        "source_positives": source_manifest.get("positives"),
        "source_negatives": source_manifest.get("negatives"),
        "config": cfg_dict,
        "shards": shard_summaries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    splits = {
        "created_at": utc_now_iso(),
        "time": cutoffs,
        "victim": {
            "strategy": "sha256(victim)_bucket_70_15_15",
            "train_buckets": "0-69",
            "val_buckets": "70-84",
            "test_buckets": "85-99",
        },
        "day_counts": dict(sorted(day_counts.items())),
        "label_counts": dict(label_counts),
    }
    (out_dir / "splits.json").write_text(json.dumps(splits, indent=2), encoding="utf-8")
    (out_dir / "feature_schema.json").write_text(json.dumps(schema_description(), indent=2), encoding="utf-8")
    source_hashes = {
        "created_at": utc_now_iso(),
        "hash_large_sources": bool(args.hash_large_sources),
        "source_manifest": file_record(source_manifest_path, True),
        "dataset_cache": file_record(Path(args.dataset_cache), bool(args.hash_large_sources)),
        "token_metadata": file_record(Path(args.token_metadata), True) if args.token_metadata else {},
        "shards_dir": str(Path(args.shards_dir)),
    }
    (out_dir / "source_hashes.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
    print(f"wrote Colab training package to {out_dir}")
    print(f"rows={exported_rows:,} positives={positives:,} negatives={negatives:,} found={found:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
