#!/usr/bin/env python
import argparse
import json
import os
import sys

import requests


def rpc_call(url, key, method, params):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Drpc-Key"] = key
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("DRPC_HTTP_URL", ""))
    ap.add_argument("--key", default=os.getenv("DRPC_KEY", ""))
    args = ap.parse_args()

    if not args.url:
        print("missing --url or DRPC_HTTP_URL")
        return 1
    result = rpc_call(args.url, args.key, "eth_blockNumber", [])
    print(json.dumps({"ok": True, "eth_blockNumber": result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
