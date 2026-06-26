#!/usr/bin/env python3
"""Build a recent active protected-account Counterparty file from Ethereum RPC.

The live benchmark parser currently observes direct ERC-20 transfer calldata in
pending transactions, so this utility scans recent full blocks and decodes the
same two selectors instead of using Transfer logs as the primary source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests


TRANSFER_SELECTOR = "a9059cbb"
TRANSFER_FROM_SELECTOR = "23b872dd"
ZERO_ADDRESS = "0x" + "0" * 40


class RetryableRPCError(RuntimeError):
    pass


class NonRetryableRPCError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_env_file(path: str) -> dict[str, str]:
    if not path or not os.path.exists(path):
        return {}
    out: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, value = trimmed.split("=", 1)
            value = value.strip().strip('"').strip("'")
            out[key.strip()] = value
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_utc = utc_now().strftime("%Y%m%dT%H%M%SZ")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=".env", help="Optional .env file containing DRPC_HTTP_URL and DRPC_KEY.")
    ap.add_argument("--rpc-url", default="", help="Ethereum JSON-RPC HTTP URL. Defaults to DRPC_HTTP_URL.")
    ap.add_argument("--rpc-key", default="", help="Optional dRPC key header. Defaults to DRPC_KEY.")
    ap.add_argument("--out", default=f"results/live_active_protected_accounts_{default_utc}.json")
    ap.add_argument("--manifest", default="", help="Manifest path. Defaults to <out>.manifest.json.")
    ap.add_argument("--from-block", type=int, default=0, help="Inclusive block number. Overrides --lookback-blocks.")
    ap.add_argument("--to-block", type=int, default=0, help="Inclusive block number. Defaults to latest-confirmations.")
    ap.add_argument("--lookback-blocks", type=int, default=7200, help="Recent blocks to scan when --from-block is unset.")
    ap.add_argument("--confirmations", type=int, default=2, help="Blocks to stay behind latest for reorg stability.")
    ap.add_argument("--max-victims", type=int, default=50)
    ap.add_argument("--min-counterparties", type=int, default=10)
    ap.add_argument("--max-counterparties-per-victim", type=int, default=200)
    ap.add_argument("--max-rows", type=int, default=10000, help="Global row cap after per-victim limits; 0 means unlimited.")
    ap.add_argument("--metadata-limit", type=int, default=250, help="Max unique token contracts to enrich with ERC-20 metadata.")
    ap.add_argument("--skip-token-metadata", action="store_true")
    ap.add_argument("--include-zero-value", action="store_true", help="Keep zero-value ERC-20 calls. Default excludes them.")
    ap.add_argument("--one-way", action="store_true", help="Only add sender->recipient, not the reverse relation.")
    ap.add_argument("--allow-contract-victims", action="store_true", help="Do not filter selected victims with eth_getCode.")
    ap.add_argument("--contract-probe-limit", type=int, default=1000, help="Max candidate victims to classify with eth_getCode.")
    ap.add_argument("--batch-size", type=int, default=3, help="JSON-RPC batch size for block reads. dRPC free tier caps this at 3.")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--retries", type=int, default=6)
    ap.add_argument("--sleep-ms", type=int, default=0, help="Optional pause after each RPC request.")
    ap.add_argument("--progress-every", type=int, default=100, help="Progress print interval in blocks; 0 disables.")
    return ap.parse_args(argv)


class RPCClient:
    def __init__(self, url: str, key: str, timeout: float, retries: int, sleep_ms: int):
        self.url = url.strip()
        self.key = key.strip()
        self.timeout = timeout
        self.retries = retries
        self.sleep_ms = sleep_ms
        self.session = requests.Session()
        self.rpc_items = 0
        self.http_requests = 0

    def call(self, method: str, params: list[Any]) -> Any:
        return self.batch([(method, params)])[0]

    def batch(self, requests: list[tuple[str, list[Any]]]) -> list[Any]:
        if not self.url:
            raise RuntimeError("missing RPC URL; set DRPC_HTTP_URL in .env or pass --rpc-url")
        if not requests:
            return []
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Drpc-Key"] = self.key
        if len(requests) == 1:
            payload: Any = {"jsonrpc": "2.0", "id": 1, "method": requests[0][0], "params": requests[0][1]}
        else:
            payload = [
                {"jsonrpc": "2.0", "id": idx + 1, "method": method, "params": params}
                for idx, (method, params) in enumerate(requests)
            ]
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                self.http_requests += 1
                self.rpc_items += len(requests)
                resp = self.session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RetryableRPCError(f"RPC HTTP {resp.status_code}: {resp.text[:300]}")
                if 400 <= resp.status_code < 500:
                    raise NonRetryableRPCError(f"RPC HTTP {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
                body = resp.json()
                if isinstance(body, dict):
                    body_items = [body]
                elif isinstance(body, list):
                    body_items = body
                else:
                    raise NonRetryableRPCError(f"unexpected RPC response type: {type(body).__name__}")
                results_by_id: dict[int, Any] = {}
                for item in body_items:
                    if not isinstance(item, dict):
                        raise NonRetryableRPCError("unexpected RPC batch item type")
                    if item.get("error"):
                        message = item["error"].get("message", "")
                        code = item["error"].get("code", "")
                        if "too many" in message.lower() or "rate" in message.lower():
                            raise RetryableRPCError(f"RPC error {code}: {message}")
                        raise NonRetryableRPCError(f"RPC error {code}: {message}")
                    results_by_id[int(item.get("id", 1))] = item.get("result")
                results = [results_by_id[idx + 1] for idx in range(len(requests))]
                if self.sleep_ms > 0:
                    time.sleep(self.sleep_ms / 1000.0)
                return results
            except NonRetryableRPCError:
                raise
            except Exception as exc:  # pragma: no cover - depends on provider/network behavior
                last_error = exc
                if attempt >= self.retries:
                    break
                wait = min(30.0, 0.75 * (2**attempt))
                time.sleep(wait)
        names = ",".join(method for method, _ in requests)
        raise RuntimeError(f"{names} failed after retries: {last_error}") from last_error


def parse_hex_int(value: str | None) -> int:
    if not value:
        return 0
    return int(value, 16)


def normalize_address(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    s = value.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 40:
        return ""
    if any(ch not in "0123456789abcdef" for ch in s):
        return ""
    return "0x" + s


def abi_word_address(word: str) -> str:
    if len(word) != 64:
        return ""
    return normalize_address("0x" + word[-40:])


@dataclass
class DecodedTransfer:
    method: str
    sender: str
    recipient: str
    token: str
    value_raw: int


def decode_direct_erc20_transfer(tx: dict[str, Any]) -> DecodedTransfer | None:
    token = normalize_address(tx.get("to"))
    if not token:
        return None
    sender_tx = normalize_address(tx.get("from"))
    data = str(tx.get("input") or "").strip().lower()
    if data.startswith("0x"):
        data = data[2:]
    if len(data) < 8:
        return None
    selector = data[:8]
    args = data[8:]
    if selector == TRANSFER_SELECTOR:
        if len(args) < 128 or not sender_tx:
            return None
        recipient = abi_word_address(args[:64])
        value_raw = int(args[64:128], 16)
        return DecodedTransfer("transfer", sender_tx, recipient, token, value_raw)
    if selector == TRANSFER_FROM_SELECTOR:
        if len(args) < 192:
            return None
        sender = abi_word_address(args[:64])
        recipient = abi_word_address(args[64:128])
        value_raw = int(args[128:192], 16)
        return DecodedTransfer("transferFrom", sender, recipient, token, value_raw)
    return None


@dataclass
class AggRow:
    victim: str
    recipient: str
    token: str
    observed_freq: int = 0
    last_seen: dt.datetime = field(default_factory=lambda: dt.datetime.fromtimestamp(0, dt.timezone.utc))
    last_block: int = 0
    last_tx_hash: str = ""
    methods: set[str] = field(default_factory=set)


def add_counterparty(
    agg: dict[tuple[str, str, str], AggRow],
    victim: str,
    recipient: str,
    token: str,
    seen_at: dt.datetime,
    block_number: int,
    tx_hash: str,
    method: str,
) -> None:
    if not victim or not recipient or not token:
        return
    if victim == recipient or victim == ZERO_ADDRESS or recipient == ZERO_ADDRESS:
        return
    key = (victim, recipient, token)
    row = agg.get(key)
    if row is None:
        row = AggRow(victim=victim, recipient=recipient, token=token)
        agg[key] = row
    row.observed_freq += 1
    row.methods.add(method)
    if seen_at >= row.last_seen:
        row.last_seen = seen_at
        row.last_block = block_number
        row.last_tx_hash = tx_hash


def victim_metrics(rows: dict[tuple[str, str, str], AggRow]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "observed_freq": 0,
            "unique_counterparties": set(),
            "unique_tokens": set(),
            "last_seen": dt.datetime.fromtimestamp(0, dt.timezone.utc),
        }
    )
    for row in rows.values():
        item = metrics[row.victim]
        item["observed_freq"] += row.observed_freq
        item["unique_counterparties"].add(row.recipient)
        item["unique_tokens"].add(row.token)
        if row.last_seen > item["last_seen"]:
            item["last_seen"] = row.last_seen
    return metrics


def account_has_code(client: RPCClient, address: str, tag: str = "latest") -> bool:
    code = client.call("eth_getCode", [address, tag])
    return isinstance(code, str) and code not in ("", "0x", "0X")


def select_victims(
    metrics: dict[str, dict[str, Any]],
    client: RPCClient,
    max_victims: int,
    min_counterparties: int,
    allow_contract_victims: bool,
    contract_probe_limit: int,
) -> tuple[list[str], dict[str, bool]]:
    candidates = []
    for victim, item in metrics.items():
        unique_counterparties = len(item["unique_counterparties"])
        if unique_counterparties < min_counterparties:
            continue
        candidates.append(
            (
                victim,
                unique_counterparties,
                int(item["observed_freq"]),
                item["last_seen"],
                len(item["unique_tokens"]),
            )
        )
    candidates.sort(key=lambda x: (x[1], x[2], x[3], x[4], x[0]), reverse=True)

    code_cache: dict[str, bool] = {}
    selected: list[str] = []
    probed = 0
    for victim, _, _, _, _ in candidates:
        if len(selected) >= max_victims:
            break
        if not allow_contract_victims:
            if probed >= contract_probe_limit:
                break
            probed += 1
            has_code = account_has_code(client, victim)
            code_cache[victim] = has_code
            if has_code:
                continue
        selected.append(victim)
    return selected, code_cache


def decode_abi_string(result: Any) -> str:
    if not isinstance(result, str):
        return ""
    data = result.strip()
    if data.startswith("0x"):
        data = data[2:]
    if not data:
        return ""
    try:
        if len(data) >= 128:
            offset = int(data[:64], 16)
            start = offset * 2
            if start + 64 <= len(data):
                length = int(data[start : start + 64], 16)
                raw = data[start + 64 : start + 64 + length * 2]
                return bytes.fromhex(raw).decode("utf-8", errors="replace").strip("\x00").strip()
        raw_static = data[:64]
        return bytes.fromhex(raw_static).decode("utf-8", errors="replace").strip("\x00").strip()
    except Exception:
        return ""


def fetch_token_metadata(client: RPCClient, tokens: list[str], limit: int) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    selectors = {
        "name": "0x06fdde03",
        "symbol": "0x95d89b41",
        "decimals": "0x313ce567",
    }
    out: dict[str, dict[str, Any]] = {}
    stats = {"attempted": 0, "success": 0, "failed": 0, "skipped_limit": max(0, len(tokens) - max(0, limit))}
    for token in tokens[: max(0, limit)]:
        stats["attempted"] += 1
        item: dict[str, Any] = {"symbol": "", "name": "", "decimals": 0, "metadata_missing": True}
        try:
            name_result = client.call("eth_call", [{"to": token, "data": selectors["name"]}, "latest"])
            symbol_result = client.call("eth_call", [{"to": token, "data": selectors["symbol"]}, "latest"])
            decimals_result = client.call("eth_call", [{"to": token, "data": selectors["decimals"]}, "latest"])
            item["name"] = decode_abi_string(name_result)
            item["symbol"] = decode_abi_string(symbol_result)
            if isinstance(decimals_result, str) and decimals_result not in ("", "0x"):
                item["decimals"] = int(decimals_result, 16)
            item["metadata_missing"] = not bool(item["name"] or item["symbol"] or item["decimals"])
            if item["metadata_missing"]:
                stats["failed"] += 1
            else:
                stats["success"] += 1
        except Exception:
            stats["failed"] += 1
        out[token] = item
    for token in tokens[max(0, limit) :]:
        out[token] = {"symbol": "", "name": "", "decimals": 0, "metadata_missing": True}
    return out, stats


def build_rows(
    agg: dict[tuple[str, str, str], AggRow],
    selected_victims: list[str],
    max_counterparties_per_victim: int,
    max_rows: int,
) -> list[AggRow]:
    victim_order = {victim: idx for idx, victim in enumerate(selected_victims)}
    per_victim: dict[str, list[AggRow]] = defaultdict(list)
    for row in agg.values():
        if row.victim in victim_order:
            per_victim[row.victim].append(row)

    rows: list[AggRow] = []
    for victim in selected_victims:
        items = per_victim.get(victim, [])
        items.sort(key=lambda r: (r.observed_freq, r.last_seen, r.recipient, r.token), reverse=True)
        rows.extend(items[: max(0, max_counterparties_per_victim)])

    rows.sort(key=lambda r: (victim_order.get(r.victim, 10**9), -r.observed_freq, -int(r.last_seen.timestamp()), r.recipient, r.token))
    if max_rows > 0:
        rows = rows[:max_rows]
    return rows


def provider_host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: str, payload: Any) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    env = load_env_file(args.env)
    rpc_url = args.rpc_url or env.get("DRPC_HTTP_URL", "")
    rpc_key = args.rpc_key or env.get("DRPC_KEY", "")
    if not rpc_url:
        print("missing RPC URL; set DRPC_HTTP_URL in .env or pass --rpc-url", file=sys.stderr)
        return 2

    started = utc_now()
    client = RPCClient(rpc_url, rpc_key, args.timeout, args.retries, args.sleep_ms)
    latest = parse_hex_int(client.call("eth_blockNumber", []))
    to_block = args.to_block or max(0, latest - max(0, args.confirmations))
    from_block = args.from_block or max(0, to_block - max(1, args.lookback_blocks) + 1)
    if from_block > to_block:
        raise RuntimeError(f"invalid block range: {from_block} > {to_block}")
    block_batch_size = max(1, min(3, args.batch_size))

    agg: dict[tuple[str, str, str], AggRow] = {}
    decoded_calls = 0
    skipped_zero_value = 0
    skipped_invalid = 0
    scanned_transactions = 0

    scanned_blocks = 0
    skipped_rpc_blocks: list[int] = []
    skipped_rpc_errors: list[dict[str, Any]] = []
    for batch_start in range(from_block, to_block + 1, block_batch_size):
        numbers = list(range(batch_start, min(to_block, batch_start + block_batch_size - 1) + 1))
        try:
            blocks = client.batch([("eth_getBlockByNumber", [hex(number), True]) for number in numbers])
        except Exception as exc:
            skipped_rpc_blocks.extend(numbers)
            if len(skipped_rpc_errors) < 25:
                skipped_rpc_errors.append({"blocks": numbers, "error": str(exc)[:300]})
            print(
                json.dumps(
                    {
                        "skipped_rpc_blocks": numbers,
                        "error": str(exc)[:240],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            continue
        for number, block in zip(numbers, blocks):
            scanned_blocks += 1
            if not block:
                continue
            timestamp = dt.datetime.fromtimestamp(parse_hex_int(block.get("timestamp")), dt.timezone.utc)
            transactions = block.get("transactions") or []
            for tx in transactions:
                if not isinstance(tx, dict):
                    continue
                scanned_transactions += 1
                decoded = decode_direct_erc20_transfer(tx)
                if decoded is None:
                    continue
                if decoded.sender in ("", ZERO_ADDRESS) or decoded.recipient in ("", ZERO_ADDRESS):
                    skipped_invalid += 1
                    continue
                if decoded.value_raw == 0 and not args.include_zero_value:
                    skipped_zero_value += 1
                    continue
                decoded_calls += 1
                tx_hash = str(tx.get("hash") or "")
                add_counterparty(agg, decoded.sender, decoded.recipient, decoded.token, timestamp, number, tx_hash, decoded.method)
                if not args.one_way:
                    add_counterparty(agg, decoded.recipient, decoded.sender, decoded.token, timestamp, number, tx_hash, decoded.method)
            if args.progress_every > 0 and scanned_blocks % args.progress_every == 0:
                print(
                    json.dumps(
                        {
                            "scanned_blocks": scanned_blocks,
                            "current_block": number,
                            "decoded_direct_erc20_calls": decoded_calls,
                            "counterparty_rows_seen": len(agg),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    metrics = victim_metrics(agg)
    selected_victims, code_cache = select_victims(
        metrics,
        client,
        args.max_victims,
        args.min_counterparties,
        args.allow_contract_victims,
        args.contract_probe_limit,
    )
    selected_rows = build_rows(agg, selected_victims, args.max_counterparties_per_victim, args.max_rows)
    unique_tokens = sorted({row.token for row in selected_rows})

    metadata: dict[str, dict[str, Any]] = {}
    metadata_stats = {"attempted": 0, "success": 0, "failed": 0, "skipped_limit": 0}
    if args.skip_token_metadata:
        metadata = {token: {"symbol": "", "name": "", "decimals": 0, "metadata_missing": True} for token in unique_tokens}
        metadata_stats["skipped_limit"] = len(unique_tokens)
    else:
        metadata, metadata_stats = fetch_token_metadata(client, unique_tokens, args.metadata_limit)

    output_rows = []
    for row in selected_rows:
        md = metadata.get(row.token, {"symbol": "", "name": "", "decimals": 0, "metadata_missing": True})
        item = {
            "last_seen": iso(row.last_seen),
            "observed_freq": row.observed_freq,
            "recipient": row.recipient,
            "token": row.token,
            "victim": row.victim,
        }
        if md.get("symbol"):
            item["token_symbol"] = md["symbol"]
        if md.get("name"):
            item["token_name"] = md["name"]
        if md.get("decimals"):
            item["token_decimals"] = int(md["decimals"])
        if md.get("metadata_missing", True):
            item["metadata_missing"] = True
        output_rows.append(item)

    write_json(args.out, output_rows)
    manifest_path = args.manifest or args.out + ".manifest.json"
    finished = utc_now()
    selected_summary = []
    for victim in selected_victims:
        item = metrics[victim]
        selected_summary.append(
            {
                "victim": victim,
                "observed_freq": int(item["observed_freq"]),
                "unique_counterparties": len(item["unique_counterparties"]),
                "unique_tokens": len(item["unique_tokens"]),
                "last_seen": iso(item["last_seen"]),
                "has_code": code_cache.get(victim, False),
            }
        )
    manifest = {
        "artifact": "active_protected_accounts",
        "mode": "full-block-direct-erc20-calldata",
        "selectors": {
            "transfer": "0x" + TRANSFER_SELECTOR,
            "transferFrom": "0x" + TRANSFER_FROM_SELECTOR,
        },
        "provider_http_host": provider_host(rpc_url),
        "generated_at_utc": iso(finished),
        "started_at_utc": iso(started),
        "duration_seconds": (finished - started).total_seconds(),
        "latest_block_at_start": latest,
        "from_block": from_block,
        "to_block": to_block,
        "requested_blocks": max(0, to_block - from_block + 1),
        "scanned_blocks": scanned_blocks,
        "skipped_rpc_blocks": len(skipped_rpc_blocks),
        "skipped_rpc_block_numbers": skipped_rpc_blocks[:200],
        "skipped_rpc_block_errors": skipped_rpc_errors,
        "scanned_transactions": scanned_transactions,
        "decoded_direct_erc20_calls": decoded_calls,
        "skipped_zero_value_calls": skipped_zero_value,
        "skipped_invalid_calls": skipped_invalid,
        "raw_counterparty_rows": len(agg),
        "candidate_victims": len(metrics),
        "selected_victims": len(selected_victims),
        "selected_rows": len(output_rows),
        "unique_tokens": len(unique_tokens),
        "bidirectional": not args.one_way,
        "exclude_zero_value": not args.include_zero_value,
        "exclude_contract_victims": not args.allow_contract_victims,
        "contract_probe_limit": args.contract_probe_limit,
        "token_metadata": metadata_stats,
        "filters": {
            "max_victims": args.max_victims,
            "min_counterparties": args.min_counterparties,
            "max_counterparties_per_victim": args.max_counterparties_per_victim,
            "max_rows": args.max_rows,
            "metadata_limit": args.metadata_limit,
            "confirmations": args.confirmations,
            "block_batch_size": block_batch_size,
        },
        "selected_victim_summary": selected_summary,
        "output_path": args.out,
        "output_sha256": sha256_file(args.out),
        "rpc_items": client.rpc_items,
        "http_requests": client.http_requests,
    }
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "ok": True,
                "out": args.out,
                "manifest": manifest_path,
                "rows": len(output_rows),
                "selected_victims": len(selected_victims),
                "decoded_direct_erc20_calls": decoded_calls,
                "from_block": from_block,
                "http_requests": client.http_requests,
                "rpc_items": client.rpc_items,
                "to_block": to_block,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
