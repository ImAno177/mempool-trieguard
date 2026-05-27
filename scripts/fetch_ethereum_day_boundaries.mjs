#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function loadEnvFile(envPath) {
  if (!fs.existsSync(envPath)) return {};
  const out = {};
  const text = fs.readFileSync(envPath, "utf8");
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx < 0) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    value = value.replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1");
    out[key] = value;
  }
  return out;
}

function dayStart(timestampSec) {
  const ms = Math.floor(timestampSec) * 1000;
  const d = new Date(ms);
  return Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000);
}

function dayLabel(timestampSec) {
  return new Date(timestampSec * 1000).toISOString().slice(0, 10);
}

function iso(timestampSec) {
  return new Date(timestampSec * 1000).toISOString();
}

function csvEscape(value) {
  const text = String(value);
  if (/[",\n\r]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

function readCache(cachePath) {
  if (!cachePath || !fs.existsSync(cachePath)) return {};
  return JSON.parse(fs.readFileSync(cachePath, "utf8"));
}

function writeCache(cachePath, cache) {
  if (!cachePath) return;
  fs.mkdirSync(path.dirname(cachePath), { recursive: true });
  fs.writeFileSync(cachePath, JSON.stringify(cache, null, 2), "utf8");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function mapLimit(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const idx = next;
      next += 1;
      results[idx] = await fn(items[idx], idx);
    }
  }
  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker());
  await Promise.all(workers);
  return results;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const env = loadEnvFile(args.env || ".env");
  const rpcUrl = args["rpc-url"] || env.DRPC_HTTP_URL || env.ETH_RPC_URL || env.WEB3_PROVIDER_URI;
  if (!rpcUrl) {
    throw new Error("RPC URL not found. Set DRPC_HTTP_URL in .env or pass --rpc-url.");
  }
  if (args.insecure) {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
  }

  const minBlock = Number.parseInt(args["min-block"], 10);
  const maxBlock = Number.parseInt(args["max-block"], 10);
  if (!Number.isFinite(minBlock) || !Number.isFinite(maxBlock) || minBlock <= 0 || maxBlock < minBlock) {
    throw new Error("Pass a valid --min-block and --max-block range.");
  }
  const outPath = args.out || "results/statistics_20260524/ethereum_day_boundaries.csv";
  const cachePath = args.cache || "results/statistics_20260524/block_timestamp_cache.json";
  const cache = readCache(cachePath);
  let rpcCalls = 0;

  async function rpc(method, params) {
    const maxRetries = Number.parseInt(args.retries || "8", 10);
    let lastError = null;
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
      try {
        const res = await fetch(rpcUrl, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
        });
        if (!res.ok) {
          throw new Error(`RPC HTTP ${res.status}`);
        }
        const body = await res.json();
        if (body.error) {
          throw new Error(`RPC error ${body.error.code}: ${body.error.message}`);
        }
        return body.result;
      } catch (err) {
        lastError = err;
        if (attempt >= maxRetries) break;
        const waitMs = Math.min(30000, 750 * 2 ** attempt);
        await sleep(waitMs);
      }
    }
    throw lastError;
  }

  async function rpcBatch(requests) {
    const maxRetries = Number.parseInt(args.retries || "8", 10);
    let lastError = null;
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
      try {
        const res = await fetch(rpcUrl, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(requests),
        });
        if (!res.ok) {
          throw new Error(`RPC HTTP ${res.status}`);
        }
        const body = await res.json();
        if (!Array.isArray(body)) {
          throw new Error("RPC endpoint did not return a batch response");
        }
        return body;
      } catch (err) {
        lastError = err;
        if (attempt >= maxRetries) break;
        const waitMs = Math.min(30000, 750 * 2 ** attempt);
        await sleep(waitMs);
      }
    }
    throw lastError;
  }

  async function blockTimestamp(blockNumber) {
    const key = String(blockNumber);
    if (cache[key] !== undefined) return Number(cache[key]);
    const hex = `0x${blockNumber.toString(16)}`;
    const block = await rpc("eth_getBlockByNumber", [hex, false]);
    if (!block || !block.timestamp) {
      throw new Error(`missing timestamp for block ${blockNumber}`);
    }
    const ts = Number.parseInt(block.timestamp, 16);
    cache[key] = ts;
    rpcCalls += 1;
    if (rpcCalls % 100 === 0) writeCache(cachePath, cache);
    return ts;
  }

  async function blockTimestamps(blockNumbers) {
    const unique = [...new Set(blockNumbers.filter((n) => cache[String(n)] === undefined))];
    const batchSize = Number.parseInt(args["batch-size"] || "64", 10);
    const singleConcurrency = Number.parseInt(args.concurrency || "12", 10);
    for (let offset = 0; offset < unique.length; offset += batchSize) {
      const chunk = unique.slice(offset, offset + batchSize);
      if (chunk.length === 0) continue;
      if (chunk.length === 1) {
        await blockTimestamp(chunk[0]);
        continue;
      }
      if (!args["no-batch"]) {
        try {
          const requests = chunk.map((blockNumber, i) => ({
            jsonrpc: "2.0",
            id: i + 1,
            method: "eth_getBlockByNumber",
            params: [`0x${blockNumber.toString(16)}`, false],
          }));
          const responses = await rpcBatch(requests);
          const byId = new Map(responses.map((item) => [item.id, item]));
          for (let i = 0; i < chunk.length; i += 1) {
            const blockNumber = chunk[i];
            const response = byId.get(i + 1);
            if (!response) throw new Error(`missing batch response for block ${blockNumber}`);
            if (response.error) {
              throw new Error(`RPC error ${response.error.code}: ${response.error.message}`);
            }
            if (!response.result || !response.result.timestamp) {
              throw new Error(`missing timestamp for block ${blockNumber}`);
            }
            cache[String(blockNumber)] = Number.parseInt(response.result.timestamp, 16);
            rpcCalls += 1;
          }
        } catch (err) {
          console.error(`batch failed (${err.message || String(err)}); falling back to single-call RPC`);
          await mapLimit(chunk, singleConcurrency, (blockNumber) => blockTimestamp(blockNumber));
        }
      } else {
        await mapLimit(chunk, singleConcurrency, (blockNumber) => blockTimestamp(blockNumber));
      }
      if (rpcCalls % 256 === 0) writeCache(cachePath, cache);
    }
  }

  async function firstBlockAtOrAfter(targetTs, low, high) {
    let lo = low;
    let hi = high + 1;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      const ts = await blockTimestamp(mid);
      if (ts >= targetTs) hi = mid;
      else lo = mid + 1;
    }
    return lo;
  }

  async function firstBlocksAtOrAfter(targets, low, high) {
    const states = targets.map((targetTs) => ({
      targetTs,
      lo: low,
      hi: high + 1,
    }));
    let unresolved = states.filter((state) => state.lo < state.hi).length;
    while (unresolved > 0) {
      const mids = [];
      for (const state of states) {
        if (state.lo < state.hi) {
          mids.push(Math.floor((state.lo + state.hi) / 2));
        }
      }
      await blockTimestamps(mids);
      unresolved = 0;
      for (const state of states) {
        if (state.lo >= state.hi) continue;
        const mid = Math.floor((state.lo + state.hi) / 2);
        const ts = Number(cache[String(mid)]);
        if (ts >= state.targetTs) state.hi = mid;
        else state.lo = mid + 1;
        if (state.lo < state.hi) unresolved += 1;
      }
    }
    return states.map((state) => state.lo);
  }

  const minTs = await blockTimestamp(minBlock);
  const maxTs = await blockTimestamp(maxBlock);
  const firstDay = dayStart(minTs);
  const lastDay = dayStart(maxTs);
  const rows = [];
  const targetDays = [];
  for (let dayTs = firstDay; dayTs <= lastDay + 86400; dayTs += 86400) {
    targetDays.push(dayTs);
  }
  const boundaryStarts = args.sequential
    ? null
    : await firstBlocksAtOrAfter(targetDays, minBlock, maxBlock);

  let previousStart = minBlock;
  for (let dayIndex = 0; dayIndex < targetDays.length - 1; dayIndex += 1) {
    const dayTs = targetDays[dayIndex];
    const nextDayTs = dayTs + 86400;
    const startBlock = Math.max(
      minBlock,
      boundaryStarts
        ? boundaryStarts[dayIndex]
        : await firstBlockAtOrAfter(dayTs, previousStart, maxBlock),
    );
    const nextStart = boundaryStarts
      ? boundaryStarts[dayIndex + 1]
      : await firstBlockAtOrAfter(nextDayTs, startBlock, maxBlock);
    const endBlock = Math.min(maxBlock, nextStart - 1);
    if (startBlock <= maxBlock && endBlock >= startBlock) {
      await blockTimestamps([startBlock, endBlock]);
      const startBlockTs = Number(cache[String(startBlock)]);
      const endBlockTs = Number(cache[String(endBlock)]);
      rows.push({
        day: dayLabel(dayTs),
        start_block: startBlock,
        end_block: endBlock,
        day_start_utc: iso(dayTs),
        next_day_start_utc: iso(nextDayTs),
        start_block_timestamp_utc: iso(startBlockTs),
        end_block_timestamp_utc: iso(endBlockTs),
      });
      previousStart = startBlock;
    }
  }

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  const header = [
    "day",
    "start_block",
    "end_block",
    "day_start_utc",
    "next_day_start_utc",
    "start_block_timestamp_utc",
    "end_block_timestamp_utc",
  ];
  const lines = [header.join(",")];
  for (const row of rows) {
    lines.push(header.map((key) => csvEscape(row[key])).join(","));
  }
  fs.writeFileSync(outPath, `${lines.join("\n")}\n`, "utf8");
  writeCache(cachePath, cache);
  console.log(`wrote ${rows.length} day boundaries to ${outPath}`);
  console.log(`cached ${Object.keys(cache).length} block timestamps (${rpcCalls} new RPC calls)`);
}

main().catch((err) => {
  console.error(err.message || String(err));
  process.exit(1);
});
