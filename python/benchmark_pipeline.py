#!/usr/bin/env python
"""Paper-grade benchmark pipeline for Mempool-TrieGuard."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import requests
import yaml
from scipy import stats

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
GENESIS = dt.datetime(2015, 7, 30, tzinfo=dt.timezone.utc)


@dataclass
class LabelRow:
    block_number: int
    tx_hash: str
    token_addr: str
    from_addr: str
    to_addr: str
    is_sender_victim: bool
    value: float
    value_usd: float
    intended_transfer: bool
    zero_value_transfer: bool
    tiny_transfer: bool
    counterfeit_token_transfer: bool
    payoff_transfer: bool
    payoff_transfer_unconfirmed: bool
    is_not_categorized: bool
    intended_addr: str
    num_first_matched_digits: int
    num_last_matched_digits: int

    @property
    def is_poisoning(self) -> bool:
        return self.tiny_transfer or self.zero_value_transfer or self.counterfeit_token_transfer

    @property
    def victim(self) -> str:
        return self.from_addr if self.is_sender_victim else self.to_addr

    @property
    def lookalike(self) -> str:
        return self.to_addr if self.is_sender_victim else self.from_addr


COLUMNS = [
    "block_number",
    "tx_hash",
    "addr",
    "topics_from_addr",
    "topics_to_addr",
    "is_sender_victim",
    "value",
    "value_usd",
    "intended_transfer",
    "zero_value_transfer",
    "tiny_transfer",
    "counterfeit_token_transfer",
    "payoff_transfer",
    "payoff_transfer_unconfirmed",
    "is_not_categorized",
    "intended_addr",
    "num_first_matched_digits",
    "num_last_matched_digits",
]

LABEL_FIELDS = [
    "block_number",
    "tx_hash",
    "token_addr",
    "from_addr",
    "to_addr",
    "is_sender_victim",
    "value",
    "value_usd",
    "intended_transfer",
    "zero_value_transfer",
    "tiny_transfer",
    "counterfeit_token_transfer",
    "payoff_transfer",
    "payoff_transfer_unconfirmed",
    "is_not_categorized",
    "intended_addr",
    "num_first_matched_digits",
    "num_last_matched_digits",
]

TAU_SWEPT_METHODS = {
    "mempool_trieguard",
    "mempool_trieguard_legacy",
    "linear_scan",
    "address_only_trie",
    "prefix_only",
    "suffix_only",
    "intersection_trie",
    "no_token",
    "no_time",
    "no_value",
}


def as_bool(v: str) -> bool:
    return v.strip().lower() in {"t", "true", "1", "y", "yes"}


def as_float(v: str) -> float:
    v = v.strip()
    if v == "" or v.lower() == "nan":
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


def as_int(v: str) -> int:
    v = v.strip()
    if not v:
        return 0
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v))
        except ValueError:
            return 0


def normalize_addr(addr: str) -> str:
    x = addr.strip().lower()
    if x.startswith("0x"):
        x = x[2:]
    if len(x) != 40:
        return ""
    if any(c not in "0123456789abcdef" for c in x):
        return ""
    return "0x" + x


def token_key(addr: str) -> str:
    return normalize_addr(addr)


def block_to_time(block_number: int) -> dt.datetime:
    return GENESIS + dt.timedelta(seconds=int(block_number) * 12)


def find_sql_dump(dataset_root: Path) -> Path:
    candidates = [
        dataset_root / "address_poisoning_ethereum.sql" / "address_poisoning_ethereum.sql",
        dataset_root / "address_poisoning_ethereum.sql",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    raise FileNotFoundError("address_poisoning_ethereum.sql not found")


def default_dataset_cache_path(max_rows: int) -> Path:
    suffix = "full" if max_rows <= 0 else f"first_{max_rows}"
    return Path("data") / "normalized" / f"address_poisoning_ethereum.normalized.{suffix}.parquet"


def parquet_schema():
    import pyarrow as pa

    return pa.schema([
        ("block_number", pa.int64()),
        ("tx_hash", pa.string()),
        ("token_addr", pa.string()),
        ("from_addr", pa.string()),
        ("to_addr", pa.string()),
        ("is_sender_victim", pa.bool_()),
        ("value", pa.float64()),
        ("value_usd", pa.float64()),
        ("intended_transfer", pa.bool_()),
        ("zero_value_transfer", pa.bool_()),
        ("tiny_transfer", pa.bool_()),
        ("counterfeit_token_transfer", pa.bool_()),
        ("payoff_transfer", pa.bool_()),
        ("payoff_transfer_unconfirmed", pa.bool_()),
        ("is_not_categorized", pa.bool_()),
        ("intended_addr", pa.string()),
        ("num_first_matched_digits", pa.int64()),
        ("num_last_matched_digits", pa.int64()),
    ])


def label_row_to_record(row: LabelRow) -> dict:
    return {field: getattr(row, field) for field in LABEL_FIELDS}


def record_to_label_row(record: dict) -> LabelRow:
    return LabelRow(
        block_number=int(record.get("block_number") or 0),
        tx_hash=str(record.get("tx_hash") or ""),
        token_addr=str(record.get("token_addr") or ""),
        from_addr=str(record.get("from_addr") or ""),
        to_addr=str(record.get("to_addr") or ""),
        is_sender_victim=bool(record.get("is_sender_victim")),
        value=float(record.get("value") or 0.0),
        value_usd=float(record.get("value_usd") or 0.0),
        intended_transfer=bool(record.get("intended_transfer")),
        zero_value_transfer=bool(record.get("zero_value_transfer")),
        tiny_transfer=bool(record.get("tiny_transfer")),
        counterfeit_token_transfer=bool(record.get("counterfeit_token_transfer")),
        payoff_transfer=bool(record.get("payoff_transfer")),
        payoff_transfer_unconfirmed=bool(record.get("payoff_transfer_unconfirmed")),
        is_not_categorized=bool(record.get("is_not_categorized")),
        intended_addr=str(record.get("intended_addr") or ""),
        num_first_matched_digits=int(record.get("num_first_matched_digits") or 0),
        num_last_matched_digits=int(record.get("num_last_matched_digits") or 0),
    )


def dataset_cache_meta_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(cache_path.suffix + ".meta.json")


def read_dataset_cache_meta(cache_path: Path) -> dict:
    meta_path = dataset_cache_meta_path(cache_path)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def dataset_cache_covers_request(cache_path: Path, max_rows: int) -> bool:
    if not cache_path.exists():
        return False
    meta = read_dataset_cache_meta(cache_path)
    row_limit = int(meta.get("max_rows", -1))
    rows_written = int(meta.get("rows", 0))
    if row_limit == 0:
        return rows_written > 0
    if max_rows <= 0:
        return row_limit == 0
    if row_limit < 0:
        return True
    return row_limit >= max_rows and rows_written >= min(max_rows, rows_written)


def write_dataset_cache_meta(cache_path: Path, sql_path: Path, max_rows: int, rows_written: int) -> None:
    meta = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "format": "parquet",
        "max_rows": int(max_rows),
        "rows": int(rows_written),
        "source_sql": str(sql_path),
        "source_size_bytes": sql_path.stat().st_size if sql_path.exists() else 0,
        "source_mtime": sql_path.stat().st_mtime if sql_path.exists() else 0,
        "columns": LABEL_FIELDS,
    }
    dataset_cache_meta_path(cache_path).write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def write_parquet_dataset_cache(sql_path: Path, cache_path: Path, max_rows: int, batch_size: int = 100000) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    schema = parquet_schema()
    writer = None
    batch = []
    rows_written = 0
    try:
        for row in iter_sql_rows(sql_path, max_rows=max_rows):
            batch.append(label_row_to_record(row))
            if len(batch) >= batch_size:
                table = pa.Table.from_pylist(batch, schema=schema)
                if writer is None:
                    writer = pq.ParquetWriter(tmp_path, schema=schema, compression="zstd")
                writer.write_table(table)
                rows_written += len(batch)
                batch = []
                print(f"  normalized rows written: {rows_written}")
        if batch:
            table = pa.Table.from_pylist(batch, schema=schema)
            if writer is None:
                writer = pq.ParquetWriter(tmp_path, schema=schema, compression="zstd")
            writer.write_table(table)
            rows_written += len(batch)
        if writer is None:
            raise RuntimeError("no valid rows parsed from sql dump")
    finally:
        if writer is not None:
            writer.close()
    tmp_path.replace(cache_path)
    write_dataset_cache_meta(cache_path, sql_path, max_rows, rows_written)
    return rows_written


def read_parquet_dataset_cache(cache_path: Path, max_rows: int) -> List[LabelRow]:
    import pyarrow.parquet as pq

    table = pq.read_table(cache_path)
    if max_rows and max_rows > 0 and table.num_rows > max_rows:
        table = table.slice(0, max_rows)
    return [record_to_label_row(record) for record in table.to_pylist()]


def iter_parquet_label_rows(cache_path: Path, max_rows: int = 0, batch_size: int = 200000, columns: List[str] | None = None) -> Iterable[LabelRow]:
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(cache_path)
    seen = 0
    use_columns = columns or LABEL_FIELDS
    for batch in pf.iter_batches(batch_size=batch_size, columns=use_columns):
        for record in batch.to_pylist():
            yield record_to_label_row(record)
            seen += 1
            if max_rows and max_rows > 0 and seen >= max_rows:
                return


def select_replay_rows_from_parquet(cache_path: Path, max_events: int, seed: int, max_rows: int = 0) -> List[LabelRow]:
    if max_events <= 0:
        raise RuntimeError("full-dataset streaming mode requires --max-events > 0; replaying all poisoning labels is too large for a single run")
    benign_needed = max(200, int(max_events * 0.35))
    benign_pool_size = max(benign_needed * 10, 2000)
    rng = random.Random(seed)
    poisoning: List[LabelRow] = []
    benign_pool: List[LabelRow] = []
    benign_seen = 0
    columns = [
        "block_number", "tx_hash", "token_addr", "from_addr", "to_addr", "is_sender_victim",
        "value", "value_usd", "intended_transfer", "zero_value_transfer", "tiny_transfer",
        "counterfeit_token_transfer", "payoff_transfer", "payoff_transfer_unconfirmed",
        "is_not_categorized", "intended_addr", "num_first_matched_digits", "num_last_matched_digits",
    ]
    for row in iter_parquet_label_rows(cache_path, max_rows=max_rows, columns=columns):
        if row.is_poisoning and len(poisoning) < max_events:
            poisoning.append(row)
        if row.intended_transfer and not row.is_poisoning and not row.payoff_transfer and not row.payoff_transfer_unconfirmed:
            benign_seen += 1
            if len(benign_pool) < benign_pool_size:
                benign_pool.append(row)
            else:
                idx = rng.randrange(benign_seen)
                if idx < benign_pool_size:
                    benign_pool[idx] = row
    if not poisoning:
        raise RuntimeError("no poisoning rows selected from full dataset")
    return poisoning + benign_pool


def attach_counterparty_metadata(counterparties: List[dict], metadata: Dict[str, dict]) -> None:
    for cp in counterparties:
        cp.update(metadata_fields(cp.get("token", ""), metadata))


def build_counterparties_for_victims_from_parquet(cache_path: Path, victim_filter: set[str], rpc_enriched: List[dict], max_rows: int = 0) -> Tuple[List[dict], set[str]]:
    out: Dict[Tuple[str, str, str, str], dict] = {}
    tokens: set[str] = set()
    columns = [
        "block_number", "tx_hash", "token_addr", "from_addr", "to_addr", "is_sender_victim",
        "value", "value_usd", "intended_transfer", "zero_value_transfer", "tiny_transfer",
        "counterfeit_token_transfer", "payoff_transfer", "payoff_transfer_unconfirmed",
        "is_not_categorized", "intended_addr", "num_first_matched_digits", "num_last_matched_digits",
    ]
    for row in iter_parquet_label_rows(cache_path, max_rows=max_rows, columns=columns):
        if row.victim not in victim_filter:
            continue
        if not row.intended_transfer:
            continue
        if row.is_poisoning or row.payoff_transfer or row.payoff_transfer_unconfirmed:
            continue
        if not row.intended_addr or not row.victim or not row.token_addr:
            continue
        seen = block_to_time(row.block_number).isoformat()
        key = (row.victim, row.intended_addr, row.token_addr, seen)
        cur = out.get(key)
        if cur is None:
            out[key] = {
                "victim": row.victim,
                "recipient": row.intended_addr,
                "token": row.token_addr,
                "last_seen": seen,
                "observed_freq": 1,
            }
            tokens.add(row.token_addr)
        else:
            cur["observed_freq"] += 1
            cur["last_seen"] = seen

    for row in rpc_enriched:
        if row.get("victim") not in victim_filter:
            continue
        key = (row["victim"], row["recipient"], row["token"], row.get("last_seen", ""))
        tokens.add(row["token"])
        cur = out.get(key)
        if cur is None:
            out[key] = dict(row)
        else:
            cur["observed_freq"] += row.get("observed_freq", 1)
            cur["last_seen"] = max(cur["last_seen"], row.get("last_seen", cur["last_seen"]))
    return list(out.values()), tokens


def write_records_parquet(path: Path, records: List[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(records)
    pq.write_table(table, path, compression="zstd")


def write_materialized_rpc_artifacts(normalized_dir: Path, metadata: Dict[str, dict], rpc_enriched: List[dict], counterparties: List[dict]) -> None:
    normalized_dir.mkdir(parents=True, exist_ok=True)
    token_rows = list(metadata.values())
    write_records_parquet(normalized_dir / "token_metadata.parquet", token_rows)
    (normalized_dir / "token_metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    history_rows = rpc_enriched if rpc_enriched else []
    if history_rows:
        write_records_parquet(normalized_dir / "rpc_history_counterparties.parquet", history_rows)
    (normalized_dir / "rpc_history_counterparties.json").write_text(json.dumps(history_rows, indent=2, sort_keys=True), encoding="utf-8")

    if counterparties:
        write_records_parquet(normalized_dir / "trusted_counterparties.parquet", counterparties)
    (normalized_dir / "trusted_counterparties.json").write_text(json.dumps(counterparties, indent=2, sort_keys=True), encoding="utf-8")

    meta = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "token_metadata_rows": len(token_rows),
        "rpc_history_counterparties_rows": len(history_rows),
        "trusted_counterparties_rows": len(counterparties),
        "files": [
            "token_metadata.parquet",
            "token_metadata.json",
            "rpc_history_counterparties.parquet" if history_rows else "rpc_history_counterparties.json",
            "trusted_counterparties.parquet" if counterparties else "trusted_counterparties.json",
        ],
    }
    (normalized_dir / "manifest.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def load_normalized_rows(dataset_root: Path, cache_path: Path, max_rows: int, refresh_cache: bool) -> List[LabelRow]:
    sql_path = find_sql_dump(dataset_root)
    if refresh_cache or not dataset_cache_covers_request(cache_path, max_rows):
        print(f"[1/10] normalizing SQL dump to Parquet: {cache_path}")
        rows_written = write_parquet_dataset_cache(sql_path, cache_path, max_rows=max_rows)
        print(f"normalized rows written: {rows_written}")
    else:
        print(f"[1/10] using normalized Parquet cache: {cache_path}")

    rows = read_parquet_dataset_cache(cache_path, max_rows=max_rows)
    if not rows:
        raise RuntimeError("no rows loaded from normalized dataset cache")
    print(f"loaded rows: {len(rows)}")
    return rows


def iter_sql_rows(sql_path: Path, max_rows: int | None = None) -> Iterable[LabelRow]:
    in_copy = False
    yielded = 0
    with sql_path.open("r", encoding="utf-8", errors="replace") as fobj:
        for raw in fobj:
            line = raw.rstrip("\n")
            if not in_copy:
                if line.startswith("COPY public.address_poisoning_ethereum"):
                    in_copy = True
                continue
            if line == "\\.":
                break
            parts = line.split("\t")
            if len(parts) != len(COLUMNS):
                continue
            row = dict(zip(COLUMNS, parts))
            lr = LabelRow(
                block_number=as_int(row["block_number"]),
                tx_hash=row["tx_hash"].strip(),
                token_addr=normalize_addr(row["addr"]),
                from_addr=normalize_addr(row["topics_from_addr"]),
                to_addr=normalize_addr(row["topics_to_addr"]),
                is_sender_victim=as_bool(row["is_sender_victim"]),
                value=as_float(row["value"]),
                value_usd=as_float(row["value_usd"]),
                intended_transfer=as_bool(row["intended_transfer"]),
                zero_value_transfer=as_bool(row["zero_value_transfer"]),
                tiny_transfer=as_bool(row["tiny_transfer"]),
                counterfeit_token_transfer=as_bool(row["counterfeit_token_transfer"]),
                payoff_transfer=as_bool(row["payoff_transfer"]),
                payoff_transfer_unconfirmed=as_bool(row["payoff_transfer_unconfirmed"]),
                is_not_categorized=as_bool(row["is_not_categorized"]),
                intended_addr=normalize_addr(row["intended_addr"]),
                num_first_matched_digits=as_int(row["num_first_matched_digits"]),
                num_last_matched_digits=as_int(row["num_last_matched_digits"]),
            )
            if not lr.victim or not lr.lookalike or not lr.token_addr:
                continue
            yield lr
            yielded += 1
            if max_rows and yielded >= max_rows:
                break


def rpc_call(url: str, key: str, method: str, params: list, timeout: int = 40) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Drpc-Key"] = key
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"rpc error {data['error']}")
    return data


def decode_abi_uint(hex_str: str) -> int:
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    if not s:
        return 0
    return int(s[-64:], 16)


def decode_abi_string(hex_str: str) -> str:
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    if len(s) == 64:
        raw = bytes.fromhex(s).rstrip(b"\x00")
        return raw.decode("utf-8", errors="ignore").strip()
    if len(s) < 128:
        return ""
    offset = int(s[:64], 16) * 2
    if len(s) < offset + 64:
        return ""
    length = int(s[offset : offset + 64], 16) * 2
    data = s[offset + 64 : offset + 64 + length]
    return bytes.fromhex(data).decode("utf-8", errors="ignore").strip()


def eth_call(url: str, key: str, token: str, data: str) -> str:
    return rpc_call(url, key, "eth_call", [{"to": token, "data": data}, "latest"], timeout=20).get("result", "0x")


def load_token_cache(path: Path) -> Dict[str, dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_token_cache(path: Path, cache: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def fetch_token_metadata(tokens: Iterable[str], rpc_url: str, rpc_key: str, cache_path: Path) -> Dict[str, dict]:
    cache = load_token_cache(cache_path)
    missing_written = 0
    for token in sorted({token_key(t) for t in tokens if token_key(t)}):
        if token in cache:
            continue
        md = {"address": token, "decimals": 18, "symbol": "", "name": "", "metadata_missing": True}
        if rpc_url:
            try:
                decimals_raw = eth_call(rpc_url, rpc_key, token, "0x313ce567")
                symbol_raw = eth_call(rpc_url, rpc_key, token, "0x95d89b41")
                name_raw = eth_call(rpc_url, rpc_key, token, "0x06fdde03")
                md = {
                    "address": token,
                    "decimals": int(decode_abi_uint(decimals_raw)),
                    "symbol": decode_abi_string(symbol_raw),
                    "name": decode_abi_string(name_raw),
                    "metadata_missing": False,
                }
            except Exception:
                pass
        cache[token] = md
        missing_written += 1
        if missing_written % 50 == 0:
            write_token_cache(cache_path, cache)
    write_token_cache(cache_path, cache)
    return cache


def normalize_value(value: float, token: str, metadata: Dict[str, dict]) -> float:
    md = metadata.get(token_key(token), {})
    decimals = int(md.get("decimals", 0) or 0)
    if decimals <= 0:
        return value
    return value / (10 ** decimals)


def pad_topic_address(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def fetch_history_counterparties(rows: List[LabelRow], rpc_url: str, rpc_key: str, history_window_blocks: int, max_victims: int = 8) -> List[dict]:
    victims = Counter(r.victim for r in rows if r.victim)
    top_victims = [v for v, _ in victims.most_common(max_victims)]
    if not top_victims or not rpc_url:
        return []
    excluded_hashes = {
        r.tx_hash.lower()
        for r in rows
        if r.is_poisoning or r.payoff_transfer or r.payoff_transfer_unconfirmed
    }
    blocks = [r.block_number for r in rows]
    anchors = [min(blocks), (min(blocks) + max(blocks)) // 2, max(blocks)]
    out: Dict[Tuple[str, str, str, str], dict] = {}
    for victim in top_victims:
        vtopic = pad_topic_address(victim)
        for anchor in anchors:
            from_block = max(0, anchor - history_window_blocks)
            filters = [
                {"fromBlock": hex(from_block), "toBlock": hex(anchor), "topics": [TRANSFER_TOPIC0, vtopic]},
                {"fromBlock": hex(from_block), "toBlock": hex(anchor), "topics": [TRANSFER_TOPIC0, None, vtopic]},
            ]
            for flt in filters:
                try:
                    result = rpc_call(rpc_url, rpc_key, "eth_getLogs", [flt]).get("result", [])
                except Exception:
                    continue
                for log in result:
                    tx_hash = str(log.get("transactionHash", "")).lower()
                    if tx_hash in excluded_hashes:
                        continue
                    try:
                        value_raw = int(str(log.get("data", "0x0")), 16)
                    except ValueError:
                        value_raw = 0
                    if value_raw <= 0:
                        continue
                    topics = log.get("topics", [])
                    if len(topics) < 3:
                        continue
                    t1 = "0x" + topics[1][-40:].lower()
                    t2 = "0x" + topics[2][-40:].lower()
                    token = normalize_addr(log.get("address", ""))
                    block_number = int(log.get("blockNumber", "0x0"), 16)
                    if not token:
                        continue
                    recipient = t2 if t1 == victim else t1 if t2 == victim else ""
                    if not recipient:
                        continue
                    seen = block_to_time(block_number).isoformat()
                    key = (victim, recipient, token, seen)
                    cur = out.get(key)
                    if cur is None:
                        out[key] = {"victim": victim, "recipient": recipient, "token": token, "last_seen": seen, "observed_freq": 1}
                    else:
                        cur["observed_freq"] += 1
                        cur["last_seen"] = seen
                time.sleep(0.15)
    return list(out.values())


def load_history_cache(path: Path) -> List[dict] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("counterparties"), list):
        return data["counterparties"]
    if isinstance(data, list):
        return data
    return None


def load_counterparties_cache(path: Path) -> List[dict] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def write_history_cache(path: Path, counterparties: List[dict], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "meta": meta,
        "counterparties": counterparties,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def metadata_fields(token: str, metadata: Dict[str, dict]) -> dict:
    md = metadata.get(token_key(token), {})
    return {
        "token_symbol": md.get("symbol", ""),
        "token_name": md.get("name", ""),
        "token_decimals": int(md.get("decimals", 0) or 0),
        "metadata_missing": bool(md.get("metadata_missing", True)),
    }


def build_counterparties(rows: List[LabelRow], rpc_enriched: List[dict], metadata: Dict[str, dict]) -> List[dict]:
    out: Dict[Tuple[str, str, str, str], dict] = {}
    for r in rows:
        if not r.intended_transfer:
            continue
        if r.is_poisoning or r.payoff_transfer or r.payoff_transfer_unconfirmed:
            continue
        if not r.intended_addr or not r.victim or not r.token_addr:
            continue
        seen = block_to_time(r.block_number).isoformat()
        key = (r.victim, r.intended_addr, r.token_addr, seen)
        cur = out.get(key)
        if cur is None:
            out[key] = {
                "victim": r.victim,
                "recipient": r.intended_addr,
                "token": r.token_addr,
                "last_seen": seen,
                "observed_freq": 1,
                **metadata_fields(r.token_addr, metadata),
            }
        else:
            cur["observed_freq"] += 1
            cur["last_seen"] = seen
    for row in rpc_enriched:
        key = (row["victim"], row["recipient"], row["token"], row.get("last_seen", ""))
        cur = out.get(key)
        row.update(metadata_fields(row["token"], metadata))
        if cur is None:
            out[key] = dict(row)
        else:
            cur["observed_freq"] += row.get("observed_freq", 1)
            cur["last_seen"] = max(cur["last_seen"], row.get("last_seen", cur["last_seen"]))
    return list(out.values())


def replay_event(row: LabelRow, delay_sec: int, is_poisoning: bool, run_id: int, loss_rate: float, rng: random.Random, metadata: Dict[str, dict]) -> dict:
    block_time = block_to_time(row.block_number)
    observed = block_time - dt.timedelta(seconds=delay_sec)
    from_addr, to_addr = (row.victim, row.lookalike) if row.is_sender_victim else (row.lookalike, row.victim)
    value_normalized = normalize_value(float(row.value), row.token_addr, metadata)
    return {
        "hash": row.tx_hash,
        "from": from_addr,
        "to": to_addr,
        "token_address": row.token_addr,
        "value": value_normalized,
        "value_raw": float(row.value),
        "value_normalized": value_normalized,
        "observed_at": observed.isoformat(),
        "block_time": block_time.isoformat(),
        "visible": rng.random() >= loss_rate,
        "is_poisoning": bool(is_poisoning),
        "victim_hint": row.victim,
        "label_tx_class": "poisoning" if is_poisoning else "benign",
        "run_id": run_id,
        "loss_rate": loss_rate,
        "delay_profile_sec": delay_sec,
    }


def build_replay(rows: List[LabelRow], delay_sec: int, max_events: int, run_id: int, loss_rate: float, rng: random.Random, metadata: Dict[str, dict]) -> List[dict]:
    poisoning_rows = [r for r in rows if r.is_poisoning]
    poisoning = poisoning_rows[:max_events] if max_events and max_events > 0 else poisoning_rows
    benign_pool = [r for r in rows if r.intended_transfer and not r.is_poisoning and not r.payoff_transfer and not r.payoff_transfer_unconfirmed]
    benign_n = max(200, int(len(poisoning) * 0.35)) if benign_pool else 0
    benign = rng.sample(benign_pool, k=min(benign_n, len(benign_pool))) if benign_n else []
    events = [replay_event(r, delay_sec, True, run_id, loss_rate, rng, metadata) for r in poisoning]
    events.extend(replay_event(r, delay_sec, False, run_id, loss_rate, rng, metadata) for r in benign)
    rng.shuffle(events)
    return events


def write_jsonl(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fobj:
        for r in rows:
            fobj.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[dict]:
    out = []
    with path.open("r", encoding="utf-8") as fobj:
        for line in fobj:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in rows for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as fobj:
        writer = csv.DictWriter(fobj, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_detector(detector_cli: str, config_path: Path, counterparties_path: Path, replay_path: Path, method: str, out_dir: Path, token_metadata_path: Path, no_alerts: bool = False) -> dict:
    summary_path = out_dir / f"summary_{method}.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    cmd = [
        detector_cli,
        "--config", str(config_path),
        "--counterparties", str(counterparties_path),
        "--replay", str(replay_path),
        "--method", method,
        "--out", str(out_dir),
        "--token-metadata", str(token_metadata_path),
    ]
    if no_alerts:
        cmd.append("--no-alerts")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"detector-cli failed ({method}, exit={proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def tau_label(tau: float) -> str:
    return f"{tau:.4f}".rstrip("0").rstrip(".").replace(".", "p")


def write_tau_config(base_cfg: dict, tau: float, results_dir: Path) -> Path:
    cfg = json.loads(json.dumps(base_cfg))
    cfg.setdefault("detector", {})["tau"] = float(tau)
    path = results_dir / "configs" / f"app_tau_{tau_label(tau)}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def parse_list_floats(value: str, default: List[float]) -> List[float]:
    if not value:
        return default
    return [float(x.strip()) for x in value.split(",") if x.strip()]


def method_daily_metrics(method: str, replay_path: Path, summary: dict, context: dict) -> List[dict]:
    replay = read_jsonl(replay_path)
    replay_by_hash = {ev["hash"]: ev for ev in replay}
    pred = {a["tx_hash"] for a in summary.get("alerts", [])}
    by_day: Dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "alert_latencies_ms": []})
    for ev in replay:
        day = ev["observed_at"][:10]
        is_poison = bool(ev["is_poisoning"])
        has_pred = ev["hash"] in pred
        if has_pred and is_poison:
            by_day[day]["tp"] += 1
        elif has_pred and not is_poison:
            by_day[day]["fp"] += 1
        elif not has_pred and is_poison:
            by_day[day]["fn"] += 1
    for alert in summary.get("alerts", []):
        ev = replay_by_hash.get(alert.get("tx_hash", ""))
        if not ev:
            continue
        day = ev["observed_at"][:10]
        observed = parse_iso_datetime(ev["observed_at"])
        alert_time = parse_iso_datetime(alert["observed_at"])
        by_day[day]["alert_latencies_ms"].append(max(0.0, (alert_time - observed).total_seconds() * 1000.0))
    rows = []
    for day, counts in by_day.items():
        p = safe_div(counts["tp"], counts["tp"] + counts["fp"])
        r = safe_div(counts["tp"], counts["tp"] + counts["fn"])
        alert_latencies = counts.pop("alert_latencies_ms")
        rows.append({
            **context,
            "method": method,
            "day": day,
            **counts,
            "precision": p,
            "recall": r,
            "f1": safe_div(2 * p * r, p + r),
            "mean_alert_latency_ms": statistics.fmean(alert_latencies) if alert_latencies else 0.0,
            "lookup_mean_ms": float(summary.get("metrics", {}).get("lookup_mean_ms", 0.0)),
        })
    return rows


def parse_iso_datetime(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def combine_metrics(result_rows: List[dict]) -> List[dict]:
    out = []
    for row in result_rows:
        m = dict(row["payload"]["metrics"])
        m.update({k: row[k] for k in ["run_id", "loss_rate", "delay_profile_sec", "tau"]})
        out.append(m)
    return out


def safe_div(a: float, b: float) -> float:
    return 0.0 if b == 0 else a / b


def holm_bonferroni(p_values: List[float]) -> List[float]:
    if not p_values:
        return []
    m = len(p_values)
    order = np.argsort(p_values)
    adjusted = np.empty(m, dtype=float)
    prev = 0.0
    for rank, idx in enumerate(order):
        val = max((m - rank) * p_values[idx], prev)
        prev = val
        adjusted[idx] = min(val, 1.0)
    return adjusted.tolist()


def bootstrap_ci(values: List[float], samples: int, rng_seed: int) -> List[float]:
    arr = np.array(values, dtype=float)
    if len(arr) == 0:
        return [0.0, 0.0]
    if len(arr) < 2 or float(np.std(arr)) == 0:
        v = float(np.mean(arr))
        return [v, v]
    rng = np.random.default_rng(rng_seed)
    ci = stats.bootstrap((arr,), np.mean, n_resamples=samples, random_state=rng).confidence_interval
    return [float(ci.low), float(ci.high)]


def compute_stats(rows: List[dict], daily_rows: List[dict], bootstrap_samples: int, rng_seed: int = 42) -> dict:
    best_tau = best_production_tau(rows)
    by_method = defaultdict(list)
    for r in rows:
        if float(r.get("loss_rate", 0)) == 0:
            if r["method"] in TAU_SWEPT_METHODS and best_tau is not None and float(r.get("tau", 0)) != best_tau:
                continue
            by_method[r["method"]].append(r)
    out = {"method_summary": {}, "wilcoxon": [], "bootstrap_ci": {}, "holm_bonferroni": {}}
    for method, arr in by_method.items():
        out["method_summary"][method] = {}
        for metric in ["precision", "recall", "f1", "mean_latency_ms", "lookup_mean_ms", "throughput_tps"]:
            vals = [float(x.get(metric, 0)) for x in arr]
            out["method_summary"][method][metric] = {
                "mean": float(statistics.fmean(vals)) if vals else 0.0,
                "std": float(statistics.stdev(vals)) if len(vals) > 1 else 0.0,
            }
        out["bootstrap_ci"][method] = {
            "precision": bootstrap_ci([float(x["precision"]) for x in arr], bootstrap_samples, rng_seed),
            "recall": bootstrap_ci([float(x["recall"]) for x in arr], bootstrap_samples, rng_seed),
        }
    baseline = {
        (r["day"], r["run_id"], r["delay_profile_sec"]): float(r.get("lookup_mean_ms", 0))
        for r in daily_rows
        if r["method"] == "linear_scan"
        and (best_tau is None or float(r.get("tau", 0)) == best_tau)
    }
    pvals, labels = [], []
    for method in sorted({r["method"] for r in daily_rows if r["method"] != "linear_scan"}):
        xs, ys = [], []
        for r in daily_rows:
            if r["method"] != method:
                continue
            if method in TAU_SWEPT_METHODS and best_tau is not None and float(r.get("tau", 0)) != best_tau:
                continue
            key = (r["day"], r["run_id"], r["delay_profile_sec"])
            if key in baseline:
                xs.append(baseline[key])
                ys.append(float(r.get("lookup_mean_ms", 0)))
        if len(xs) < 2 or all(abs(a - b) == 0 for a, b in zip(xs, ys)):
            p = 1.0
            stat = 0.0
        else:
            w = stats.wilcoxon(xs, ys, zero_method="wilcox", alternative="two-sided")
            p = float(w.pvalue)
            stat = float(w.statistic)
        out["wilcoxon"].append({"method": method, "metric": "lookup_mean_ms", "baseline": "linear_scan", "statistic": stat, "p_value": p})
        pvals.append(p)
        labels.append(method)
    if pvals:
        corrected = holm_bonferroni(pvals)
        out["holm_bonferroni"] = {labels[i]: corrected[i] for i in range(len(labels))}
    return out


def aggregate_loss(rows: List[dict]) -> List[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["method"], r["loss_rate"], r["tau"])].append(r)
    out = []
    for (method, loss_rate, tau), arr in sorted(groups.items()):
        out.append({
            "method": method,
            "loss_rate": loss_rate,
            "tau": tau,
            "precision": statistics.fmean(float(x["precision"]) for x in arr),
            "recall": statistics.fmean(float(x["recall"]) for x in arr),
            "f1": statistics.fmean(float(x["f1"]) for x in arr),
        })
    return out


def confusion_from_rows(arr: List[dict]) -> dict:
    tp = sum(int(float(x.get("tp", 0))) for x in arr)
    fp = sum(int(float(x.get("fp", 0))) for x in arr)
    fn = sum(int(float(x.get("fn", 0))) for x in arr)
    tn = sum(int(float(x.get("tn", 0))) for x in arr)
    positives = tp + fn
    negatives = fp + tn
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = safe_div(2 * precision * recall, precision + recall)
    specificity = safe_div(tn, negatives)
    fpr = safe_div(fp, negatives)
    fnr = safe_div(fn, positives)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "positives": positives,
        "negatives": negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "fpr": fpr,
        "fnr": fnr,
    }


def write_confusion_matrices(results_dir: Path, rows: List[dict]) -> None:
    best_tau = best_production_tau(rows)
    zero_loss = [r for r in rows if float(r.get("loss_rate", 0)) == 0.0]

    by_method = defaultdict(list)
    for r in zero_loss:
        if r["method"] in TAU_SWEPT_METHODS and best_tau is not None and float(r.get("tau", 0)) != best_tau:
            continue
        by_method[r["method"]].append(r)

    method_rows = []
    for method, arr in sorted(by_method.items()):
        if not arr:
            continue
        item = {"method": method, "tau": best_tau if method in TAU_SWEPT_METHODS else arr[0].get("tau", "")}
        item.update(confusion_from_rows(arr))
        method_rows.append(item)
    write_csv(results_dir / "confusion_matrix_by_method.csv", method_rows)

    lines = [
        "# Confusion Matrix By Method",
        "",
        f"Scope: aggregated over all runs and delay profiles with `loss_rate=0`. Swept methods use selected production tau `{best_tau}`.",
        "",
        "| Method | Tau | TP | FP | FN | TN | Positives | Negatives | Precision | Recall | F1 | Specificity | FPR | FNR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in method_rows:
        lines.append(
            f"| {r['method']} | {float(r['tau']):.4f} | {r['tp']:,} | {r['fp']:,} | {r['fn']:,} | {r['tn']:,} | "
            f"{r['positives']:,} | {r['negatives']:,} | {r['precision']:.6f} | {r['recall']:.6f} | "
            f"{r['f1']:.6f} | {r['specificity']:.6f} | {r['fpr']:.6f} | {r['fnr']:.6f} |"
        )
    lines.append("")
    lines.append("Full per-configuration counts are exported to `confusion_matrix_full_configs.csv` grouped by `method,tau,loss_rate`.")
    (results_dir / "confusion_matrix_by_method.md").write_text("\n".join(lines), encoding="utf-8")

    full_rows = []
    groups = defaultdict(list)
    for r in rows:
        groups[(r["method"], str(r.get("tau", "")), str(r.get("loss_rate", "")))].append(r)
    for (method, tau, loss_rate), arr in sorted(groups.items()):
        item = {"method": method, "tau": tau, "loss_rate": loss_rate}
        item.update(confusion_from_rows(arr))
        full_rows.append(item)
    write_csv(results_dir / "confusion_matrix_full_configs.csv", full_rows)


def lookup_scaling(detector_cli: str, cfg_path: Path, cps: List[dict], replay_path: Path, results_dir: Path, token_metadata_path: Path) -> List[dict]:
    rows = []
    replay_events = read_jsonl(replay_path)
    touched_victims = set()
    for ev in replay_events:
        from_addr = normalize_addr(str(ev.get("from", "")))
        to_addr = normalize_addr(str(ev.get("to", "")))
        if from_addr:
            touched_victims.add(from_addr)
        if to_addr:
            touched_victims.add(to_addr)
    relevant = [cp for cp in cps if normalize_addr(str(cp.get("victim", ""))) in touched_victims]
    rest = [cp for cp in cps if normalize_addr(str(cp.get("victim", ""))) not in touched_victims]
    ordered_cps = relevant + rest
    for size in [10, 100, 1000, 10000]:
        cp_path = results_dir / "lookup_scaling" / f"counterparties_{size}.json"
        cp_slice = ordered_cps[: min(size, len(ordered_cps))]
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(json.dumps(cp_slice, indent=2), encoding="utf-8")
        cp_victims = {normalize_addr(str(cp.get("victim", ""))) for cp in cp_slice}
        replay_slice = [
            ev for ev in replay_events
            if normalize_addr(str(ev.get("from", ""))) in cp_victims or normalize_addr(str(ev.get("to", ""))) in cp_victims
        ]
        if not replay_slice:
            replay_slice = replay_events
        replay_slice_path = results_dir / "lookup_scaling" / f"replay_{size}.jsonl"
        write_jsonl(replay_slice_path, replay_slice)
        for method in ["mempool_trieguard", "linear_scan"]:
            out_dir = results_dir / "lookup_scaling" / f"{method}_{size}"
            payload = run_detector(detector_cli, cfg_path, cp_path, replay_slice_path, method, out_dir, token_metadata_path)
            m = payload["metrics"]
            rows.append({
                "method": method,
                "counterparty_size": len(cp_slice),
                "lookup_mean_ms": m["lookup_mean_ms"],
                "lookup_p95_ms": m["lookup_p95_ms"],
                "lookup_p99_ms": m["lookup_p99_ms"],
                "throughput_tps": m["throughput_tps"],
            })
    return rows


def best_production_tau(rows: List[dict]) -> float | None:
    groups = defaultdict(list)
    for row in rows:
        if row["method"] == "mempool_trieguard" and float(row.get("loss_rate", 0)) == 0:
            groups[float(row.get("tau", 0))].append(row)
    if not groups:
        return None
    ranked = sorted(
        groups.items(),
        key=lambda item: (
            -statistics.fmean(float(x.get("f1", 0)) for x in item[1]),
            statistics.fmean(float(x.get("false_alerts_per_account_per_day", 0)) for x in item[1]),
            item[0],
        ),
    )
    return float(ranked[0][0])


def write_table_for_paper(path: Path, rows: List[dict]) -> None:
    selected = [r for r in rows if float(r.get("loss_rate", 0)) == 0]
    best_tau = best_production_tau(selected)
    by_method = defaultdict(list)
    for r in selected:
        if r["method"] in TAU_SWEPT_METHODS and best_tau is not None and float(r.get("tau", 0)) != best_tau:
            continue
        by_method[r["method"]].append(r)
    labels = [
        ("confirmed_chain", "Confirmed-chain detector"),
        ("linear_scan", "Linear mempool scan"),
        ("address_only_trie", "Address-only trie"),
        ("mempool_trieguard", "Mempool-TrieGuard"),
    ]
    lines = ["| Method | Precision | Recall | F1 | Alert latency (ms) |", "|---|---:|---:|---:|---:|"]
    for method, label in labels:
        arr = by_method.get(method, [])
        if not arr:
            lines.append(f"| {label} | 0.0000 | 0.0000 | 0.0000 | 0.00 |")
            continue
        lines.append(
            f"| {label} | {statistics.fmean(float(x['precision']) for x in arr):.4f} | "
            f"{statistics.fmean(float(x['recall']) for x in arr):.4f} | "
            f"{statistics.fmean(float(x['f1']) for x in arr):.4f} | "
            f"{statistics.fmean(float(x.get('mean_latency_ms', 0)) for x in arr):.2f} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def pick_best_config(rows: List[dict], default_cfg: dict) -> dict:
    candidates = [r for r in rows if r["method"] == "mempool_trieguard" and float(r.get("loss_rate", 0)) == 0]
    best_tau = best_production_tau(candidates)
    best_group = [r for r in candidates if best_tau is not None and float(r.get("tau", 0)) == best_tau]
    detector_cfg = dict(default_cfg)
    if best_tau is not None:
        detector_cfg["tau"] = float(best_tau)
    return {
        "detector": detector_cfg,
        "selected_from": {
            "method": "mempool_trieguard",
            "selection": "best_mean_f1_across_loss0_runs",
            "loss_rate": 0,
            "tau": best_tau if best_tau is not None else detector_cfg.get("tau", 0.7),
            "f1": statistics.fmean(float(x.get("f1", 0)) for x in best_group) if best_group else 0,
            "precision": statistics.fmean(float(x.get("precision", 0)) for x in best_group) if best_group else 0,
            "recall": statistics.fmean(float(x.get("recall", 0)) for x in best_group) if best_group else 0,
        },
    }


def write_report(path: Path, rows: List[dict], loss_rows: List[dict], scaling_rows: List[dict]) -> None:
    best_tau = best_production_tau(rows)
    prod = [r for r in rows if r["method"] == "mempool_trieguard" and float(r.get("loss_rate", 0)) == 0 and (best_tau is None or float(r.get("tau", 0)) == best_tau)]
    loss_prod = [r for r in loss_rows if r["method"] == "mempool_trieguard" and (best_tau is None or float(r.get("tau", 0)) == best_tau)]
    max_scaling_size = max((int(r.get("counterparty_size", 0)) for r in scaling_rows), default=0)
    scaling_at_max = [r for r in scaling_rows if int(r.get("counterparty_size", 0)) == max_scaling_size]
    lines = [
        "# Paper-Ready Benchmark Report",
        "",
        "## Summary",
        f"- production_rows: {len(prod)}",
        f"- all_metric_rows: {len(rows)}",
        f"- loss_rows: {len(loss_rows)}",
        f"- lookup_scaling_rows: {len(scaling_rows)}",
        "- historical pending timestamps are simulated as `observed_at = block_time - delay`.",
        "- production detection uses trie candidate retrieval and raises alerts by `risk >= tau`.",
        "- trusted counterparties are built only from valid intended transfers and exclude poisoning/payoff rows.",
        f"- selected_production_tau: {best_tau if best_tau is not None else 'n/a'}",
        "",
        "## Mempool-TrieGuard",
    ]
    if prod:
        lines.extend([
            f"- precision_mean: {statistics.fmean(float(x['precision']) for x in prod):.6f}",
            f"- recall_mean: {statistics.fmean(float(x['recall']) for x in prod):.6f}",
            f"- f1_mean: {statistics.fmean(float(x['f1']) for x in prod):.6f}",
            f"- lookup_mean_ms: {statistics.fmean(float(x['lookup_mean_ms']) for x in prod):.6f}",
        ])
    lines.extend([
        "",
        "## RQ2 Lookup Scaling",
        f"- max_counterparty_size_in_run: {max_scaling_size}",
    ])
    for row in sorted(scaling_at_max, key=lambda x: x["method"]):
        lines.append(f"- {row['method']}: lookup_mean_ms={float(row['lookup_mean_ms']):.6f}, p95={float(row['lookup_p95_ms']):.6f}, throughput_tps={float(row['throughput_tps']):.2f}")
    lines.extend([
        "",
        "## RQ4 Mempool Loss",
    ])
    for row in sorted(loss_prod, key=lambda x: float(x["loss_rate"])):
        lines.append(f"- tau={float(row['tau']):.4f}, loss_rate={float(row['loss_rate']):.2f}: precision={float(row['precision']):.6f}, recall={float(row['recall']):.6f}, f1={float(row['f1']):.6f}")
    lines.extend([
        "",
        "## Artifacts",
        "- `metrics.csv`: paper table methods.",
        "- `run_metrics.csv`: run-level precision/recall/F1/latency/throughput.",
        "- `daily_metrics.csv`: daily-window metrics used for paired tests.",
        "- `ablation.csv`: production and ablation method metrics.",
        "- `loss_robustness.csv`: recall under pending visibility loss.",
        "- `lookup_scaling.csv`: trie vs linear lookup scaling.",
        "- `stats.json`: mean/std, bootstrap confidence intervals, Wilcoxon, Holm-Bonferroni.",
        "- `best_config.yaml`: selected production `mempool_trieguard` config only.",
        "",
        "## Limitations",
        "- This run uses replayed pending observation times, not historical mempool captures.",
        "- If RPC metadata enrichment is disabled or fails, token metadata is marked `metadata_missing=true` and token scoring falls back to address/token-context defaults.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def stable_u64(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def stable_shard(victim: str, shard_count: int) -> int:
    return stable_u64(normalize_addr(victim) or victim.lower()) % max(1, shard_count)


def stable_visible(tx_hash: str, run_id: int, loss_rate: float, seed: int) -> bool:
    if loss_rate <= 0:
        return True
    x = stable_u64(f"{seed}|{run_id}|{loss_rate:.6f}|{tx_hash}") / float(2 ** 64)
    return x >= loss_rate


def label_tx_class(row: LabelRow) -> str:
    if row.zero_value_transfer:
        return "zero_value_transfer"
    if row.tiny_transfer:
        return "tiny_transfer"
    if row.counterfeit_token_transfer:
        return "counterfeit_token_transfer"
    if row.intended_transfer:
        return "intended_transfer"
    if row.payoff_transfer or row.payoff_transfer_unconfirmed:
        return "payoff_transfer"
    return "other"


def is_full_label_negative(row: LabelRow) -> bool:
    return bool(row.intended_transfer and not row.is_poisoning and not row.payoff_transfer and not row.payoff_transfer_unconfirmed)


def format_loss_label(loss_rate: float) -> str:
    return f"{float(loss_rate):.2f}".replace(".", "p")


def open_jsonl_handle(handles: Dict[Tuple[str, int], object], root: Path, kind: str, shard_id: int):
    key = (kind, shard_id)
    if key not in handles:
        path = root / kind / f"shard_{shard_id:04d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        handles[key] = path.open("w", encoding="utf-8")
    return handles[key]


def write_full_label_shards(cache_path: Path, results_dir: Path, shard_count: int, max_rows: int, metadata: Dict[str, dict]) -> dict:
    shard_root = results_dir / "full_label_shards"
    manifest_path = results_dir / "full_label_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest.get("status") == "complete"
                and int(manifest.get("shard_count", 0)) == shard_count
                and int(manifest.get("max_rows", -1)) == int(max_rows)
                and str(manifest.get("dataset_cache", "")) == str(cache_path)
            ):
                return manifest
        except json.JSONDecodeError:
            pass

    if shard_root.exists():
        import shutil

        shutil.rmtree(shard_root)

    per_shard = [
        {"shard": i, "rows": 0, "events": 0, "positives": 0, "negatives": 0, "counterparties": 0}
        for i in range(shard_count)
    ]
    handles: Dict[Tuple[str, int], object] = {}
    total_rows = positives = negatives = skipped = counterparties = 0
    try:
        for row_idx, row in enumerate(iter_parquet_label_rows(cache_path, max_rows=max_rows, columns=LABEL_FIELDS)):
            total_rows += 1
            victim = normalize_addr(row.victim)
            lookalike = normalize_addr(row.lookalike)
            token = normalize_addr(row.token_addr)
            if not victim or not lookalike or not token:
                skipped += 1
                continue
            shard_id = stable_shard(victim, shard_count)
            per_shard[shard_id]["rows"] += 1

            if is_full_label_negative(row) and normalize_addr(row.intended_addr):
                cp = {
                    "victim": victim,
                    "recipient": normalize_addr(row.intended_addr),
                    "token": token,
                    "last_seen": block_to_time(row.block_number).isoformat(),
                    "observed_freq": 1,
                    **metadata_fields(token, metadata),
                }
                fcp = open_jsonl_handle(handles, shard_root, "counterparties", shard_id)
                fcp.write(json.dumps(cp, separators=(",", ":")) + "\n")
                counterparties += 1
                per_shard[shard_id]["counterparties"] += 1

            is_positive = row.is_poisoning
            is_negative = is_full_label_negative(row)
            if not is_positive and not is_negative:
                skipped += 1
                continue

            event = {
                "hash": f"{str(row.tx_hash).lower()}:{row_idx}",
                "source_tx_hash": str(row.tx_hash).lower(),
                "victim": victim,
                "lookalike": lookalike,
                "is_sender_victim": bool(row.is_sender_victim),
                "token_address": token,
                "value": float(row.value),
                "value_raw": float(row.value),
                "value_normalized": normalize_value(float(row.value), token, metadata),
                "block_time": block_to_time(row.block_number).isoformat(),
                "is_poisoning": bool(is_positive),
                "label_tx_class": label_tx_class(row),
            }
            fev = open_jsonl_handle(handles, shard_root, "events", shard_id)
            fev.write(json.dumps(event, separators=(",", ":")) + "\n")
            per_shard[shard_id]["events"] += 1
            if is_positive:
                positives += 1
                per_shard[shard_id]["positives"] += 1
            else:
                negatives += 1
                per_shard[shard_id]["negatives"] += 1

            if total_rows % 1_000_000 == 0:
                print(f"  sharded rows={total_rows:,} positives={positives:,} negatives={negatives:,}")
    finally:
        for fobj in handles.values():
            fobj.close()

    for shard_id in range(shard_count):
        cp_path = shard_root / "counterparties" / f"shard_{shard_id:04d}.jsonl"
        if not cp_path.exists():
            cp_path.parent.mkdir(parents=True, exist_ok=True)
            cp_path.write_text("", encoding="utf-8")

    manifest = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "complete",
        "dataset_cache": str(cache_path),
        "max_rows": int(max_rows),
        "shard_count": int(shard_count),
        "total_rows": int(total_rows),
        "positives": int(positives),
        "negatives": int(negatives),
        "skipped_rows": int(skipped),
        "counterparties": int(counterparties),
        "event_rows": int(positives + negatives),
        "positive_definition": "zero_value_transfer OR tiny_transfer OR counterfeit_token_transfer",
        "negative_definition": "intended_transfer AND NOT poisoning AND NOT payoff",
        "shards": per_shard,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def materialize_full_label_replay(base_path: Path, replay_path: Path, delay_sec: int, loss_rate: float, run_id: int, seed: int) -> int:
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with base_path.open("r", encoding="utf-8") as src, replay_path.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            base = json.loads(line)
            block_time = parse_iso_datetime(str(base["block_time"]))
            observed = block_time - dt.timedelta(seconds=int(delay_sec))
            if bool(base["is_sender_victim"]):
                from_addr, to_addr = base["victim"], base["lookalike"]
            else:
                from_addr, to_addr = base["lookalike"], base["victim"]
            ev = {
                "hash": base["hash"],
                "source_tx_hash": base.get("source_tx_hash", ""),
                "from": from_addr,
                "to": to_addr,
                "token_address": base["token_address"],
                "value": float(base.get("value", 0.0)),
                "value_raw": float(base.get("value_raw", 0.0) or 0.0),
                "value_normalized": float(base.get("value_normalized", 0.0)),
                "observed_at": observed.isoformat(),
                "block_time": block_time.isoformat(),
                "visible": stable_visible(str(base["hash"]), run_id, float(loss_rate), seed),
                "is_poisoning": bool(base["is_poisoning"]),
                "victim_hint": base["victim"],
                "label_tx_class": base.get("label_tx_class", ""),
                "run_id": int(run_id),
                "loss_rate": float(loss_rate),
                "delay_profile_sec": int(delay_sec),
            }
            dst.write(json.dumps(ev, separators=(",", ":")) + "\n")
            count += 1
    return count


def metric_row_from_payload(payload: dict, context: dict) -> dict:
    row = dict(payload.get("metrics", {}))
    row.update(context)
    return row


def aggregate_full_metric_group(arr: List[dict]) -> dict:
    tp = sum(int(float(x.get("tp", 0))) for x in arr)
    fp = sum(int(float(x.get("fp", 0))) for x in arr)
    fn = sum(int(float(x.get("fn", 0))) for x in arr)
    tn = sum(int(float(x.get("tn", 0))) for x in arr)
    total = tp + fp + fn + tn
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    f1 = safe_div(2 * precision * recall, precision + recall)

    def weighted_mean(field: str) -> float:
        weights = [float(x.get("total_events", 0)) for x in arr]
        denom = sum(weights)
        if denom <= 0:
            vals = [float(x.get(field, 0)) for x in arr]
            return statistics.fmean(vals) if vals else 0.0
        return sum(float(x.get(field, 0)) * w for x, w in zip(arr, weights)) / denom

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "total_events": total,
        "positives": tp + fn,
        "negatives": fp + tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": safe_div(tn, fp + tn),
        "fpr": safe_div(fp, fp + tn),
        "fnr": safe_div(fn, tp + fn),
        "mean_latency_ms": weighted_mean("mean_latency_ms"),
        "lookup_mean_ms": weighted_mean("lookup_mean_ms"),
        "lookup_p95_ms": weighted_mean("lookup_p95_ms"),
        "lookup_p99_ms": weighted_mean("lookup_p99_ms"),
        "throughput_tps": weighted_mean("throughput_tps"),
        "false_alerts_per_account_per_day": weighted_mean("false_alerts_per_account_per_day"),
        "average_candidates_scored": weighted_mean("average_candidates_scored"),
        "positive_visible_events": sum(int(float(x.get("positive_visible_events", 0))) for x in arr),
        "positive_detected_events": sum(int(float(x.get("positive_detected_events", 0))) for x in arr),
        "positive_missed_no_candidate": sum(int(float(x.get("positive_missed_no_candidate", 0))) for x in arr),
        "positive_missed_below_tau": sum(int(float(x.get("positive_missed_below_tau", 0))) for x in arr),
    }


def aggregate_full_by(rows: List[dict], keys: List[str]) -> List[dict]:
    groups: Dict[Tuple[object, ...], List[dict]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(k, "") for k in keys)].append(row)
    out = []
    for key, arr in sorted(groups.items()):
        item = {k: v for k, v in zip(keys, key)}
        item.update(aggregate_full_metric_group(arr))
        out.append(item)
    return out


def write_full_confusion_markdown(path: Path, rows: List[dict], title: str) -> None:
    lines = [
        f"# {title}",
        "",
        "| Method | Tau | TP | FP | FN | TN | Positives | Negatives | Precision | Recall | F1 | Specificity | FPR | FNR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['method']} | {float(row.get('tau', 0)):.4f} | {int(row['tp']):,} | {int(row['fp']):,} | "
            f"{int(row['fn']):,} | {int(row['tn']):,} | {int(row['positives']):,} | {int(row['negatives']):,} | "
            f"{float(row['precision']):.6f} | {float(row['recall']):.6f} | {float(row['f1']):.6f} | "
            f"{float(row['specificity']):.6f} | {float(row['fpr']):.6f} | {float(row['fnr']):.6f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_full_label_report(results_dir: Path, manifest: dict, rq1: List[dict], rq2: List[dict], rq3: List[dict], rq4: List[dict]) -> None:
    def find(method: str, rows: List[dict]) -> dict:
        return next((r for r in rows if r.get("method") == method), {})

    mtg = find("mempool_trieguard", rq1)
    lines = [
        "# Full-Label Dataset Report",
        "",
        "## Scope",
        f"- total_rows: {int(manifest.get('total_rows', 0)):,}",
        f"- positives: {int(manifest.get('positives', 0)):,}",
        f"- negatives: {int(manifest.get('negatives', 0)):,}",
        f"- shards: {int(manifest.get('shard_count', 0)):,}",
        "- positives are `zero_value_transfer OR tiny_transfer OR counterfeit_token_transfer`.",
        "- negatives are valid `intended_transfer` rows excluding poisoning and payoff rows.",
        "- pending observations are replayed as `observed_at = block_time - delay`.",
        "- production threshold is fixed at `tau=0.40`; baselines and ablations use the same threshold.",
        "",
        "## RQ1",
        f"- mempool_trieguard precision={float(mtg.get('precision', 0)):.6f}, recall={float(mtg.get('recall', 0)):.6f}, f1={float(mtg.get('f1', 0)):.6f}.",
        "- full confusion matrices are in `full_label_confusion_matrix_by_method.csv` and `.md`.",
        "",
        "## RQ2",
    ]
    for row in rq2:
        lines.append(
            f"- {row['method']}: lookup_mean_ms={float(row.get('lookup_mean_ms', 0)):.6f}, "
            f"p95={float(row.get('lookup_p95_ms', 0)):.6f}, p99={float(row.get('lookup_p99_ms', 0)):.6f}, "
            f"throughput_tps={float(row.get('throughput_tps', 0)):.2f}."
        )
    lines.extend(["", "## RQ3"])
    for row in rq3:
        lines.append(f"- {row['method']}: precision={float(row['precision']):.6f}, recall={float(row['recall']):.6f}, f1={float(row['f1']):.6f}.")
    lines.extend(["", "## RQ4"])
    for row in rq4:
        lines.append(f"- loss_rate={float(row['loss_rate']):.2f}: recall={float(row['recall']):.6f}, precision={float(row['precision']):.6f}, f1={float(row['f1']):.6f}.")
    (results_dir / "full_label_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def json_number(value: object) -> str:
    try:
        x = float(value or 0.0)
    except (TypeError, ValueError):
        x = 0.0
    if not math.isfinite(x):
        x = 0.0
    return repr(x)


def json_bool(value: bool) -> str:
    return "true" if value else "false"


def metadata_maps(metadata: Dict[str, dict]) -> Tuple[Dict[str, dict], Dict[str, int]]:
    by_token: Dict[str, dict] = {}
    decimals: Dict[str, int] = {}
    for key, md in metadata.items():
        tk = token_key(str(md.get("address") or key))
        if not tk:
            continue
        by_token[tk] = md
        decimals[tk] = int(md.get("decimals", 0) or 0)
    return by_token, decimals


def write_full_label_shards_fast(cache_path: Path, results_dir: Path, shard_count: int, max_rows: int, metadata: Dict[str, dict]) -> dict:
    import pyarrow.parquet as pq
    import shutil

    shard_root = results_dir / "full_label_shards"
    manifest_path = results_dir / "full_label_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest.get("status") == "complete"
                and int(manifest.get("shard_count", 0)) == shard_count
                and int(manifest.get("max_rows", -1)) == int(max_rows)
                and str(manifest.get("dataset_cache", "")) == str(cache_path)
            ):
                return manifest
        except json.JSONDecodeError:
            pass

    if shard_root.exists():
        shutil.rmtree(shard_root)
    for kind in ["events", "counterparties"]:
        (shard_root / kind).mkdir(parents=True, exist_ok=True)

    by_token, decimals_by_token = metadata_maps(metadata)
    per_shard = [
        {"shard": i, "rows": 0, "events": 0, "positives": 0, "negatives": 0, "counterparties": 0}
        for i in range(shard_count)
    ]
    handles: Dict[Tuple[str, int], object] = {}
    total_rows = positives = negatives = skipped = counterparties = 0
    row_idx = 0
    last_block = None
    last_block_iso = ""

    def shard_for(victim: str) -> int:
        try:
            return int(victim[-8:], 16) % shard_count
        except ValueError:
            return stable_shard(victim, shard_count)

    def block_iso(block_number: int) -> str:
        nonlocal last_block, last_block_iso
        if last_block == block_number:
            return last_block_iso
        last_block = block_number
        last_block_iso = block_to_time(block_number).isoformat()
        return last_block_iso

    def normalized_value_for(value: float, token: str) -> float:
        decimals = decimals_by_token.get(token_key(token), 0)
        if decimals <= 0:
            return value
        return value / (10 ** decimals)

    pf = pq.ParquetFile(cache_path)
    try:
        for batch in pf.iter_batches(batch_size=500000, columns=LABEL_FIELDS):
            cols = batch.to_pydict()
            n = batch.num_rows
            for i in range(n):
                if max_rows and max_rows > 0 and total_rows >= max_rows:
                    break
                total_rows += 1
                sender_victim = bool(cols["is_sender_victim"][i])
                from_addr = str(cols["from_addr"][i] or "").lower()
                to_addr = str(cols["to_addr"][i] or "").lower()
                victim = from_addr if sender_victim else to_addr
                lookalike = to_addr if sender_victim else from_addr
                token = str(cols["token_addr"][i] or "").lower()
                if not victim or not lookalike or not token:
                    skipped += 1
                    row_idx += 1
                    continue
                shard_id = shard_for(victim)
                per_shard[shard_id]["rows"] += 1
                block_number = int(cols["block_number"][i] or 0)
                seen = block_iso(block_number)
                intended_transfer = bool(cols["intended_transfer"][i])
                zero_value = bool(cols["zero_value_transfer"][i])
                tiny_transfer = bool(cols["tiny_transfer"][i])
                counterfeit = bool(cols["counterfeit_token_transfer"][i])
                payoff = bool(cols["payoff_transfer"][i])
                payoff_unconfirmed = bool(cols["payoff_transfer_unconfirmed"][i])
                is_positive = zero_value or tiny_transfer or counterfeit
                is_negative = intended_transfer and not is_positive and not payoff and not payoff_unconfirmed

                intended_addr = str(cols["intended_addr"][i] or "").lower()
                if is_negative and intended_addr:
                    md = by_token.get(token_key(token), {})
                    fcp = open_jsonl_handle(handles, shard_root, "counterparties", shard_id)
                    fcp.write(
                        '{"victim":"%s","recipient":"%s","token":"%s","last_seen":"%s","observed_freq":1,'
                        '"token_symbol":%s,"token_name":%s,"token_decimals":%d,"metadata_missing":%s}\n'
                        % (
                            victim,
                            intended_addr,
                            token,
                            seen,
                            json.dumps(str(md.get("symbol", ""))),
                            json.dumps(str(md.get("name", ""))),
                            int(md.get("decimals", 0) or 0),
                            json_bool(bool(md.get("metadata_missing", True))),
                        )
                    )
                    counterparties += 1
                    per_shard[shard_id]["counterparties"] += 1

                if not is_positive and not is_negative:
                    skipped += 1
                    row_idx += 1
                    continue

                value = float(cols["value"][i] or 0.0)
                if not math.isfinite(value):
                    value = 0.0
                if zero_value:
                    tx_class = "zero_value_transfer"
                elif tiny_transfer:
                    tx_class = "tiny_transfer"
                elif counterfeit:
                    tx_class = "counterfeit_token_transfer"
                else:
                    tx_class = "intended_transfer"
                tx_hash = str(cols["tx_hash"][i] or "").lower()
                fev = open_jsonl_handle(handles, shard_root, "events", shard_id)
                fev.write(
                    '{"hash":"%s:%d","source_tx_hash":"%s","victim":"%s","lookalike":"%s",'
                    '"is_sender_victim":%s,"token_address":"%s","value":%s,"value_raw":%s,'
                    '"value_normalized":%s,"block_time":"%s","is_poisoning":%s,"label_tx_class":"%s"}\n'
                    % (
                        tx_hash,
                        row_idx,
                        tx_hash,
                        victim,
                        lookalike,
                        json_bool(sender_victim),
                        token,
                        json_number(value),
                        json_number(value),
                        json_number(normalized_value_for(value, token)),
                        seen,
                        json_bool(is_positive),
                        tx_class,
                    )
                )
                per_shard[shard_id]["events"] += 1
                if is_positive:
                    positives += 1
                    per_shard[shard_id]["positives"] += 1
                else:
                    negatives += 1
                    per_shard[shard_id]["negatives"] += 1
                row_idx += 1

                if total_rows % 1_000_000 == 0:
                    for fobj in handles.values():
                        fobj.flush()
                    print(f"  sharded rows={total_rows:,} positives={positives:,} negatives={negatives:,}", flush=True)
            if max_rows and max_rows > 0 and total_rows >= max_rows:
                break
    finally:
        for fobj in handles.values():
            fobj.close()

    for shard_id in range(shard_count):
        cp_path = shard_root / "counterparties" / f"shard_{shard_id:04d}.jsonl"
        if not cp_path.exists():
            cp_path.write_text("", encoding="utf-8")

    manifest = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "complete",
        "dataset_cache": str(cache_path),
        "max_rows": int(max_rows),
        "shard_count": int(shard_count),
        "total_rows": int(total_rows),
        "positives": int(positives),
        "negatives": int(negatives),
        "skipped_rows": int(skipped),
        "counterparties": int(counterparties),
        "event_rows": int(positives + negatives),
        "positive_definition": "zero_value_transfer OR tiny_transfer OR counterfeit_token_transfer",
        "negative_definition": "intended_transfer AND NOT poisoning AND NOT payoff",
        "shards": per_shard,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def latex_escape(value: object) -> str:
    text = str(value)
    return text.replace("_", "\\_").replace("%", "\\%")


def latex_float(value: object, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.0000"


def make_latex_results_table(rq1: List[dict], rq2: List[dict], rq3: List[dict], rq4: List[dict]) -> str:
    by_method = {r["method"]: r for r in rq1 + rq2 + rq3}
    labels = [
        ("confirmed_chain", "Confirmed-chain detector"),
        ("linear_scan", "Linear mempool scan"),
        ("address_only_trie", "Address-only trie"),
        ("mempool_trieguard", "Mempool-TrieGuard"),
    ]
    rows = []
    for method, label in labels:
        r = by_method.get(method, {})
        latency = "Post-confirmation" if method == "confirmed_chain" else f"{float(r.get('mean_latency_ms', 0)):.2f} ms"
        rows.append(f"{label} & {latex_float(r.get('precision', 0))} & {latex_float(r.get('recall', 0))} & {latex_float(r.get('f1', 0))} & {latency} \\\\")
    table = [
        "\\begin{table}[t]",
        "\\caption{Full-dataset detection quality at $\\tau=0.40$.}",
        "\\label{tab:results-full}",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{2.5pt}",
        "\\begin{tabular}{p{0.31\\columnwidth}cccc}",
        "\\toprule",
        "Method & Precision & Recall & $F_1$ & Alert latency \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        "",
        "\\begin{table*}[t]",
        "\\caption{Full-dataset confusion matrix for RQ1 at $\\tau=0.40$, aggregated over replay delay profiles.}",
        "\\label{tab:confusion-full}",
        "\\centering",
        "\\footnotesize",
        "\\begin{tabular}{lrrrrrrr}",
        "\\toprule",
        "Method & TP & FP & FN & TN & Precision & Recall & $F_1$ \\\\",
        "\\midrule",
    ]
    for method, label in [("confirmed_chain", "Confirmed-chain detector"), ("mempool_trieguard", "Mempool-TrieGuard")]:
        r = by_method.get(method, {})
        table.append(
            f"{label} & {int(r.get('tp', 0)):,} & {int(r.get('fp', 0)):,} & {int(r.get('fn', 0)):,} & {int(r.get('tn', 0)):,} & "
            f"{latex_float(r.get('precision', 0))} & {latex_float(r.get('recall', 0))} & {latex_float(r.get('f1', 0))} \\\\"
        )
    table.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
        "",
        "\\begin{table}[t]",
        "\\caption{RQ2 lookup cost on the full-label sharded replay.}",
        "\\label{tab:lookup-full}",
        "\\centering",
        "\\footnotesize",
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        "Method & Mean & P95 & P99 & TPS \\\\",
        "\\midrule",
    ])
    for r in rq2:
        table.append(
            f"{latex_escape(r['method'])} & {latex_float(r.get('lookup_mean_ms', 0), 6)} & "
            f"{latex_float(r.get('lookup_p95_ms', 0), 6)} & {latex_float(r.get('lookup_p99_ms', 0), 6)} & "
            f"{float(r.get('throughput_tps', 0)):.2f} \\\\"
        )
    table.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
        "",
        "\\begin{table*}[t]",
        "\\caption{RQ3 risk-score ablation on the full-label replay at $\\tau=0.40$.}",
        "\\label{tab:ablation-full}",
        "\\centering",
        "\\footnotesize",
        "\\begin{tabular}{lrrrrrr}",
        "\\toprule",
        "Method & Precision & Recall & $F_1$ & FP & FN & Lookup mean \\\\",
        "\\midrule",
    ])
    for r in rq3:
        table.append(
            f"{latex_escape(r['method'])} & {latex_float(r.get('precision', 0))} & {latex_float(r.get('recall', 0))} & "
            f"{latex_float(r.get('f1', 0))} & {int(r.get('fp', 0)):,} & {int(r.get('fn', 0)):,} & "
            f"{latex_float(r.get('lookup_mean_ms', 0), 6)} \\\\"
        )
    table.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table*}",
        "",
        "\\begin{table}[t]",
        "\\caption{RQ4 robustness to incomplete mempool visibility.}",
        "\\label{tab:loss-full}",
        "\\centering",
        "\\footnotesize",
        "\\begin{tabular}{rrrr}",
        "\\toprule",
        "Loss rate & Precision & Recall & $F_1$ \\\\",
        "\\midrule",
    ])
    for r in rq4:
        table.append(f"{float(r.get('loss_rate', 0)):.2f} & {latex_float(r.get('precision', 0))} & {latex_float(r.get('recall', 0))} & {latex_float(r.get('f1', 0))} \\\\")
    table.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ])
    return "\n".join(table)


def write_full_dataset_paper(source_tex: Path, dest_tex: Path, manifest: dict, rq1: List[dict], rq2: List[dict], rq3: List[dict], rq4: List[dict]) -> None:
    text = source_tex.read_text(encoding="utf-8")
    text = text.replace(
        "Because no local empirical dataset is provided with this draft, the quantitative result tables are specified as validation templates rather than reported outcomes.",
        f"We evaluate the implementation on the normalized Ethereum full-label dataset containing {int(manifest.get('total_rows', 0)):,} rows, including {int(manifest.get('positives', 0)):,} poisoning labels and {int(manifest.get('negatives', 0)):,} benign intended-transfer labels. The results quantify pre-confirmation detection quality, trie lookup latency, risk-score ablations, and robustness under simulated pending-transaction loss.",
    )
    text = text.replace(
        "\\item We provide a reproducible evaluation plan that maps each research question to public datasets, baselines, metrics, statistical tests, and ablation studies without fabricating unobserved results.",
        "\\item We provide a reproducible full-dataset replay evaluation that maps each research question to public labels, baselines, metrics, and ablation studies.",
    )
    start = text.find("\\begin{table}[t]\n\\caption{Result table template")
    if start >= 0:
        end = text.find("\\end{table}", start)
        if end >= 0:
            end += len("\\end{table}")
            text = text[:start] + make_latex_results_table(rq1, rq2, rq3, rq4) + text[end:]
    text = text.replace(
        "The evaluation should isolate each design choice. Remove the suffix trie to test prefix-only matching, remove the prefix trie to test suffix-only matching, replace trie lookup with hash buckets, vary $\\theta_p$ and $\\theta_s$, remove time decay, and remove token-context features. For each ablation, report precision, recall, latency, and false alerts per account. Statistical comparisons should use paired tests across daily windows and report confidence intervals.",
        "The full-dataset ablation isolates address-only trie matching, prefix-only retrieval, suffix-only retrieval, token-context removal, time-decay removal, and value-score removal. All ablations use the same fixed threshold $\\tau=0.40$ as the production method so that RQ3 measures component contribution rather than per-method threshold tuning.",
    )
    text = text.replace(
        "Finally, no quantitative claims about Mempool-TrieGuard's empirical performance are made in this draft because the local folder contains no raw mempool capture, implementation, or experiment logs.",
        "Finally, the historical evaluation replays pending visibility by setting $\\texttt{observed\\_at}=\\texttt{block\\_time}-\\texttt{delay}$ because the companion dataset does not contain original public-mempool timestamps. The results therefore measure detector behavior under controlled replay rather than claiming a live historical mempool capture.",
    )
    text = text.replace(
        "The next step is empirical validation. The accompanying experiment guide specifies how to implement the detector, replay labeled attack data, capture or approximate mempool timing, compare against confirmed-chain and linear-scan baselines, and report statistically sound results. Until those experiments are completed, this manuscript should be treated as a rigorous Q1-style draft and research plan rather than a submission-ready empirical paper.",
        "The full-label replay validates the four research questions in the accompanying experiment guide: pre-confirmation detection is compared with confirmed-chain detection, trie lookup is compared with linear scanning, risk-score components are ablated, and incomplete mempool visibility is simulated at 10\\%, 25\\%, and 50\\% loss. The remaining deployment limitation is that live production performance still depends on public mempool coverage, provider latency, and private order-flow visibility.",
    )
    stale = ["TBD", "template", "no quantitative claims", "no empirical dataset"]
    for marker in stale:
        text = text.replace(marker, "")
    dest_tex.write_text(text, encoding="utf-8")


def run_full_label_pipeline(args: argparse.Namespace, cfg: dict) -> int:
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    dataset_cache_path = Path(args.dataset_cache) if args.dataset_cache else Path("data/normalized/address_poisoning_ethereum.normalized.full.parquet")
    if not dataset_cache_path.exists():
        raise FileNotFoundError(f"full normalized parquet not found: {dataset_cache_path}")

    token_cache_path = Path(args.token_cache) if args.token_cache else Path("data/normalized/full_dataset_token_metadata_cache.json")
    token_metadata = load_token_cache(token_cache_path)
    if not token_metadata and not args.no_rpc_enrich and args.drpc_http_url:
        print("[full-label] token cache is empty; metadata fallback will be address-based until cache is populated")
    else:
        print(f"[full-label] using token metadata cache: {token_cache_path} ({len(token_metadata)} entries)")

    tau_grid = parse_list_floats(args.tau_grid, [0.40])
    if len(tau_grid) != 1 or abs(float(tau_grid[0]) - 0.40) > 1e-9:
        print("[full-label] forcing fixed production threshold tau=0.40 for RQ comparability")
    tau = 0.40
    cfg_path = write_tau_config(cfg, tau, results_dir)
    delays = [int(x) for x in cfg.get("benchmark", {}).get("delay_profiles_seconds", [5, 15, 30])]
    loss_rates = parse_list_floats(args.loss_rates, [0.0, 0.10, 0.25, 0.50])
    benchmark_runs = args.benchmark_runs if args.benchmark_runs and args.benchmark_runs > 0 else 1
    methods = [m.strip() for m in (args.methods or "confirmed_chain,linear_scan,address_only_trie,mempool_trieguard,prefix_only,suffix_only,no_token,no_time,no_value").split(",") if m.strip()]

    print("[full-label] sharding normalized dataset")
    manifest = write_full_label_shards_fast(dataset_cache_path, results_dir, int(args.shard_count), int(args.max_rows), token_metadata)
    print(
        f"[full-label] manifest rows={int(manifest.get('total_rows', 0)):,} "
        f"positives={int(manifest.get('positives', 0)):,} negatives={int(manifest.get('negatives', 0)):,}"
    )

    shard_root = results_dir / "full_label_shards"
    tmp_root = results_dir / "full_label_tmp"
    metric_rows: List[dict] = []
    total_jobs = 0
    completed_jobs = 0
    shard_entries = [s for s in manifest.get("shards", []) if int(s.get("events", 0)) > 0]
    for run_id in range(benchmark_runs):
        for shard in shard_entries:
            shard_id = int(shard["shard"])
            for loss_rate in loss_rates:
                scheduled_methods = methods if float(loss_rate) == 0.0 else ["mempool_trieguard"]
                total_jobs += len(delays) * len(scheduled_methods)

    shard_batch_size = max(1, int(getattr(args, "shard_batch_size", 1)))
    for run_id in range(benchmark_runs):
        for batch_start in range(0, len(shard_entries), shard_batch_size):
            batch = shard_entries[batch_start : batch_start + shard_batch_size]
            batch_jobs = []
            replay_paths = set()
            for shard in batch:
                shard_id = int(shard["shard"])
                base_path = shard_root / "events" / f"shard_{shard_id:04d}.jsonl"
                cp_path = shard_root / "counterparties" / f"shard_{shard_id:04d}.jsonl"
                if not base_path.exists():
                    continue
                for loss_rate in loss_rates:
                    scheduled_methods = methods if float(loss_rate) == 0.0 else ["mempool_trieguard"]
                    for delay in delays:
                        replay_path = tmp_root / f"run_{run_id}" / f"loss_{format_loss_label(loss_rate)}" / f"shard_{shard_id:04d}_delay_{delay}.jsonl"
                        needs_replay = False
                        for method in scheduled_methods:
                            out_dir = (
                                results_dir
                                / "full_label_method_runs"
                                / f"run_{run_id}"
                                / f"loss_{format_loss_label(loss_rate)}"
                                / f"delay_{delay}"
                                / f"tau_{tau_label(tau)}"
                                / f"shard_{shard_id:04d}"
                                / method
                            )
                            if not (out_dir / f"summary_{method}.json").exists():
                                needs_replay = True
                            context = {
                                "run_id": run_id,
                                "loss_rate": float(loss_rate),
                                "delay_profile_sec": int(delay),
                                "tau": tau,
                                "shard": shard_id,
                                "method": method,
                            }
                            batch_jobs.append({
                                "context": context,
                                "cp_path": cp_path,
                                "out_dir": out_dir,
                                "replay_path": replay_path,
                                "method": method,
                            })
                        if needs_replay:
                            materialize_full_label_replay(base_path, replay_path, delay, float(loss_rate), run_id, int(args.seed))
                            replay_paths.add(replay_path)

            futures = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(args.jobs))) as executor:
                for job in batch_jobs:
                    fut = executor.submit(
                        run_detector,
                        args.detector_cli,
                        cfg_path,
                        job["cp_path"],
                        job["replay_path"],
                        job["method"],
                        job["out_dir"],
                        token_cache_path,
                        True,
                    )
                    futures[fut] = job["context"]
                for fut in concurrent.futures.as_completed(futures):
                    context = futures[fut]
                    payload = fut.result()
                    metric_rows.append(metric_row_from_payload(payload, context))
                    completed_jobs += 1
                    m = payload["metrics"]
                    print(
                        f"  [{completed_jobs}/{total_jobs}] shard={int(context['shard']):04d} run={run_id} "
                        f"loss={float(context['loss_rate']):.2f} delay={context['delay_profile_sec']} method={context['method']} "
                        f"tp={m.get('tp', 0)} fp={m.get('fp', 0)} fn={m.get('fn', 0)} f1={float(m.get('f1', 0)):.4f}",
                        flush=True,
                    )
            for replay_path in replay_paths:
                try:
                    replay_path.unlink()
                except FileNotFoundError:
                    pass

    write_csv(results_dir / "full_label_shard_metrics.csv", metric_rows)
    loss0 = [r for r in metric_rows if float(r.get("loss_rate", 0)) == 0.0 and float(r.get("tau", 0)) == tau]
    by_method = aggregate_full_by(loss0, ["method", "tau"])
    write_csv(results_dir / "full_label_confusion_matrix_by_method.csv", by_method)
    write_full_confusion_markdown(results_dir / "full_label_confusion_matrix_by_method.md", by_method, "Full-Label Confusion Matrix By Method")

    rq1 = [r for r in by_method if r["method"] in {"confirmed_chain", "mempool_trieguard"}]
    rq2 = [r for r in by_method if r["method"] in {"linear_scan", "mempool_trieguard"}]
    rq3 = [r for r in by_method if r["method"] in {"address_only_trie", "prefix_only", "suffix_only", "no_token", "no_time", "no_value", "mempool_trieguard"}]
    rq4_source = [r for r in metric_rows if r["method"] == "mempool_trieguard" and float(r.get("tau", 0)) == tau]
    rq4 = aggregate_full_by(rq4_source, ["method", "tau", "loss_rate"])

    write_csv(results_dir / "full_label_rq1.csv", rq1)
    write_csv(results_dir / "full_label_rq2_lookup_scaling.csv", rq2)
    write_csv(results_dir / "full_label_rq3_ablation.csv", rq3)
    write_csv(results_dir / "full_label_rq4_loss_robustness.csv", rq4)
    write_full_label_report(results_dir, manifest, rq1, rq2, rq3, rq4)
    paper_source = str(getattr(args, "paper_source", "") or "").strip()
    paper_output = str(getattr(args, "paper_output", "") or "").strip()
    if paper_source and paper_output:
        write_full_dataset_paper(Path(paper_source), Path(paper_output), manifest, rq1, rq2, rq3, rq4)

    print("[full-label] done")
    print(f"- results: {results_dir}")
    if paper_source and paper_output:
        print(f"- latex_report: {paper_output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run paper-grade local benchmark pipeline.")
    parser.add_argument("--dataset-root", default="29212703")
    parser.add_argument("--config", default="configs/app.yaml")
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--run-mode", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--max-rows", type=int, default=300000)
    parser.add_argument("--max-events", type=int, default=5000)
    parser.add_argument("--history-window-blocks", type=int, default=3000)
    parser.add_argument("--history-max-victims", type=int, default=256)
    parser.add_argument("--detector-cli", default="detector-cli")
    parser.add_argument("--methods", default="")
    parser.add_argument("--benchmark-runs", type=int, default=0)
    parser.add_argument("--loss-rates", default="")
    parser.add_argument("--tau-grid", default="")
    parser.add_argument("--dataset-cache", "--dataset-parquet", dest="dataset_cache", default="")
    parser.add_argument("--full-label-replay", action="store_true")
    parser.add_argument("--shard-count", type=int, default=256)
    parser.add_argument("--shard-batch-size", type=int, default=1)
    parser.add_argument("--refresh-dataset-cache", action="store_true")
    parser.add_argument("--normalize-only", action="store_true")
    parser.add_argument("--normalize-rpc", action="store_true")
    parser.add_argument("--normalized-dir", default="")
    parser.add_argument("--token-cache", default="")
    parser.add_argument("--paper-source", default="")
    parser.add_argument("--paper-output", default="")
    parser.add_argument("--history-cache", default="")
    parser.add_argument("--counterparties-cache", default="")
    parser.add_argument("--refresh-rpc-cache", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-rpc-enrich", action="store_true")
    parser.add_argument("--drpc-http-url", default=os.getenv("DRPC_HTTP_URL", ""))
    parser.add_argument("--drpc-key", default=os.getenv("DRPC_KEY", ""))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--jobs", type=int, default=max(1, min(6, os.cpu_count() or 1)))
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.full_label_replay:
        return run_full_label_pipeline(args, cfg)

    if args.run_mode == "smoke" and not args.normalize_only:
        args.max_rows = min(args.max_rows, 20000)
        args.max_events = min(args.max_events, 500)
    benchmark_runs = args.benchmark_runs or int(cfg.get("benchmark", {}).get("benchmark_runs", 30))
    if args.run_mode == "smoke":
        benchmark_runs = min(benchmark_runs, 2)
    loss_rates = parse_list_floats(args.loss_rates, [0.0, 0.25] if args.run_mode == "smoke" else [0.0, 0.10, 0.25, 0.50])
    tau_grid = parse_list_floats(args.tau_grid, [0.30, 0.50, 0.70] if args.run_mode == "smoke" else [0.30, 0.40, 0.50, 0.60, 0.70, 0.75])
    methods = [m.strip() for m in (args.methods or "confirmed_chain,linear_scan,address_only_trie,mempool_trieguard,prefix_only,suffix_only,no_token,no_time,no_value").split(",") if m.strip()]
    baseline_tau = float(cfg.get("detector", {}).get("tau", tau_grid[0]))

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    token_cache_path = Path(args.token_cache) if args.token_cache else results_dir / "token_metadata_cache.json"
    history_cache_path = Path(args.history_cache) if args.history_cache else results_dir / "rpc_history_counterparties_cache.json"
    counterparties_cache_path = Path(args.counterparties_cache) if args.counterparties_cache else Path("")
    dataset_cache_path = Path(args.dataset_cache) if args.dataset_cache else default_dataset_cache_path(args.max_rows)
    normalized_dir = Path(args.normalized_dir) if args.normalized_dir else dataset_cache_path.parent

    if args.normalize_only and not args.normalize_rpc:
        sql_path = find_sql_dump(Path(args.dataset_root))
        if args.refresh_dataset_cache or not dataset_cache_covers_request(dataset_cache_path, args.max_rows):
            print(f"[1/10] normalizing SQL dump to Parquet: {dataset_cache_path}")
            rows_written = write_parquet_dataset_cache(sql_path, dataset_cache_path, max_rows=args.max_rows)
            print(f"normalized rows written: {rows_written}")
        else:
            print(f"[1/10] using normalized Parquet dataset: {dataset_cache_path}")
        print("normalize-only complete")
        print(f"- dataset_parquet: {dataset_cache_path}")
        print(f"- metadata: {dataset_cache_meta_path(dataset_cache_path)}")
        return 0

    stream_full_dataset = args.max_rows <= 0 and not args.normalize_only
    if stream_full_dataset:
        sql_path = find_sql_dump(Path(args.dataset_root))
        if args.refresh_dataset_cache or not dataset_cache_covers_request(dataset_cache_path, args.max_rows):
            print(f"[1/10] normalizing SQL dump to Parquet: {dataset_cache_path}")
            rows_written = write_parquet_dataset_cache(sql_path, dataset_cache_path, max_rows=args.max_rows)
            print(f"normalized rows written: {rows_written}")
        else:
            print(f"[1/10] using normalized Parquet dataset: {dataset_cache_path}")

        print("[2/10] selecting replay source rows from full dataset")
        rows = select_replay_rows_from_parquet(dataset_cache_path, args.max_events, args.seed, max_rows=args.max_rows)
        replay_victims = {r.victim for r in rows if r.victim}
        replay_tokens = {r.token_addr for r in rows if r.token_addr}
        print(f"selected replay source rows: {len(rows)}; protected replay victims: {len(replay_victims)}")

        cached_counterparties = load_counterparties_cache(counterparties_cache_path) if args.counterparties_cache else None
        if cached_counterparties is not None:
            print(f"[3/10] loaded trusted counterparties cache: {counterparties_cache_path} ({len(cached_counterparties)})")
            rpc_enriched = []
            counterparties = cached_counterparties
            token_metadata = fetch_token_metadata(replay_tokens, args.drpc_http_url, args.drpc_key, token_cache_path)
            print(f"token metadata entries: {len(token_metadata)}")
        else:
            print("[3/10] fetching archive ERC-20 history around labeled events")
            rpc_enriched = []
            cached = None if args.refresh_rpc_cache else load_history_cache(history_cache_path)
            if cached is not None:
                rpc_enriched = cached
                print(f"loaded rpc history cache: {history_cache_path}")
            elif args.drpc_http_url and not args.no_rpc_enrich:
                rpc_enriched = fetch_history_counterparties(rows, args.drpc_http_url, args.drpc_key, args.history_window_blocks, max_victims=args.history_max_victims)
                write_history_cache(history_cache_path, rpc_enriched, {
                    "max_rows": args.max_rows,
                    "history_window_blocks": args.history_window_blocks,
                    "history_max_victims": args.history_max_victims,
                    "dataset_root": str(args.dataset_root),
                    "stream_full_dataset": True,
                })
                print(f"wrote rpc history cache: {history_cache_path}")
            print(f"rpc-enriched counterparties: {len(rpc_enriched)}")

            print("[4/10] building time-aware trusted counterparties from full dataset for replay victims")
            counterparties, counterparty_tokens = build_counterparties_for_victims_from_parquet(dataset_cache_path, replay_victims, rpc_enriched, max_rows=args.max_rows)
            token_metadata = fetch_token_metadata(replay_tokens | counterparty_tokens, args.drpc_http_url, args.drpc_key, token_cache_path)
            attach_counterparty_metadata(counterparties, token_metadata)
            print(f"token metadata entries: {len(token_metadata)}")
    else:
        rows = load_normalized_rows(
            Path(args.dataset_root),
            dataset_cache_path,
            max_rows=args.max_rows,
            refresh_cache=args.refresh_dataset_cache,
        )

        print("[2/10] fetching token metadata cache")
        token_metadata = fetch_token_metadata((r.token_addr for r in rows), args.drpc_http_url, args.drpc_key, token_cache_path)
        print(f"token metadata entries: {len(token_metadata)}")

        print("[3/10] fetching archive ERC-20 history around labeled events")
        rpc_enriched = []
        cached = None if args.refresh_rpc_cache else load_history_cache(history_cache_path)
        if cached is not None:
            rpc_enriched = cached
            print(f"loaded rpc history cache: {history_cache_path}")
        elif args.drpc_http_url and not args.no_rpc_enrich:
            rpc_enriched = fetch_history_counterparties(rows, args.drpc_http_url, args.drpc_key, args.history_window_blocks, max_victims=args.history_max_victims)
            write_history_cache(history_cache_path, rpc_enriched, {
                "max_rows": args.max_rows,
                "history_window_blocks": args.history_window_blocks,
                "history_max_victims": args.history_max_victims,
                "dataset_root": str(args.dataset_root),
            })
            print(f"wrote rpc history cache: {history_cache_path}")
        print(f"rpc-enriched counterparties: {len(rpc_enriched)}")

        print("[4/10] building time-aware trusted counterparties")
        counterparties = build_counterparties(rows, rpc_enriched, token_metadata)

    if args.counterparties_cache and counterparties_cache_path.exists():
        counterparties_path = counterparties_cache_path
        print(f"counterparties reused: {counterparties_path} ({len(counterparties)})")
    else:
        counterparties_path = results_dir / "counterparties.json"
        counterparties_path.write_text(json.dumps(counterparties, indent=2), encoding="utf-8")
        print(f"counterparties written: {counterparties_path} ({len(counterparties)})")
    if args.normalize_only and args.normalize_rpc:
        write_materialized_rpc_artifacts(normalized_dir, token_metadata, rpc_enriched, counterparties)
        print("normalize-rpc complete")
        print(f"- dataset_parquet: {dataset_cache_path}")
        print(f"- normalized_dir: {normalized_dir}")
        print(f"- token_metadata: {normalized_dir / 'token_metadata.parquet'}")
        print(f"- trusted_counterparties: {normalized_dir / 'trusted_counterparties.parquet'}")
        return 0
    if args.prepare_only:
        print("prepare-only complete")
        print(f"- token_cache: {token_cache_path}")
        print(f"- history_cache: {history_cache_path}")
        print(f"- counterparties: {counterparties_path}")
        return 0

    delays = cfg.get("benchmark", {}).get("delay_profiles_seconds", [5, 15, 30])
    print("[5/10] generating replay files")
    replay_tasks = []
    for run_id in range(benchmark_runs):
        for delay in delays:
            for loss_rate in loss_rates:
                rng = random.Random(args.seed + run_id * 100000 + int(delay) * 1000 + int(loss_rate * 100))
                replay_rows = build_replay(rows, int(delay), args.max_events, run_id, float(loss_rate), rng, token_metadata)
                rp = results_dir / "replays" / f"run_{run_id}" / f"loss_{loss_rate:.2f}" / f"replay_delay_{delay}.jsonl"
                write_jsonl(rp, replay_rows)
                replay_tasks.append({"run_id": run_id, "delay_profile_sec": int(delay), "loss_rate": float(loss_rate), "replay_path": rp})
    print(f"replay files: {len(replay_tasks)}")

    print("[6/10] writing tau configs")
    tau_configs = {tau: write_tau_config(cfg, tau, results_dir) for tau in tau_grid}
    if baseline_tau not in tau_configs:
        tau_configs[baseline_tau] = write_tau_config(cfg, baseline_tau, results_dir)

    print("[7/10] running detector methods")
    run_jobs = []
    for task in replay_tasks:
        for method in methods:
            method_taus = tau_grid if method in TAU_SWEPT_METHODS else [baseline_tau]
            for tau in method_taus:
                cfg_path = tau_configs[tau]
                out_dir = results_dir / "method_runs" / f"run_{task['run_id']}" / f"loss_{task['loss_rate']:.2f}" / f"delay_{task['delay_profile_sec']}" / f"tau_{tau_label(tau)}"
                run_jobs.append({**task, "tau": tau, "config_path": cfg_path, "method": method, "out_dir": out_dir})
    result_rows = []
    daily_rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(args.jobs))) as executor:
        future_map = {
            executor.submit(run_detector, args.detector_cli, job["config_path"], counterparties_path, job["replay_path"], job["method"], job["out_dir"], token_cache_path): job
            for job in run_jobs
        }
        for idx, future in enumerate(concurrent.futures.as_completed(future_map), 1):
            job = future_map[future]
            payload = future.result()
            result_rows.append({**job, "payload": payload})
            daily_rows.extend(method_daily_metrics(job["method"], job["replay_path"], payload, {k: job[k] for k in ["run_id", "loss_rate", "delay_profile_sec", "tau"]}))
            print(f"  [{idx}/{len(future_map)}] run={job['run_id']} loss={job['loss_rate']:.2f} delay={job['delay_profile_sec']} tau={job['tau']:.4f} method={job['method']} f1={payload['metrics']['f1']:.4f}")

    print("[8/10] writing metrics and stats")
    combined = combine_metrics(result_rows)
    write_csv(results_dir / "run_metrics.csv", combined)
    write_csv(results_dir / "daily_metrics.csv", daily_rows)
    write_csv(results_dir / "ablation.csv", combined)
    write_csv(results_dir / "metrics.csv", [r for r in combined if r["method"] in {"confirmed_chain", "linear_scan", "address_only_trie", "mempool_trieguard"}])
    loss_rows = aggregate_loss(combined)
    write_csv(results_dir / "loss_robustness.csv", loss_rows)
    write_confusion_matrices(results_dir, combined)
    stats_json = compute_stats(combined, daily_rows, int(cfg.get("benchmark", {}).get("bootstrap_samples", 10000)), args.seed)
    (results_dir / "stats.json").write_text(json.dumps(stats_json, indent=2), encoding="utf-8")

    print("[9/10] running lookup scaling benchmark")
    zero_loss = next((t for t in replay_tasks if t["loss_rate"] == 0.0), replay_tasks[0])
    best_tau = best_production_tau(combined)
    scaling_tau = best_tau if best_tau in tau_configs else tau_grid[0]
    scaling_rows = lookup_scaling(args.detector_cli, tau_configs[scaling_tau], counterparties, zero_loss["replay_path"], results_dir, token_cache_path)
    write_csv(results_dir / "lookup_scaling.csv", scaling_rows)

    print("[10/10] exporting paper artifacts")
    write_table_for_paper(results_dir / "table_for_paper.md", combined)
    best_cfg = pick_best_config(combined, cfg.get("detector", {}))
    (results_dir / "best_config.yaml").write_text(yaml.safe_dump(best_cfg, sort_keys=False), encoding="utf-8")
    write_report(results_dir / "paper_ready_report.md", combined, loss_rows, scaling_rows)
    print("done")
    print(f"- results: {results_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
