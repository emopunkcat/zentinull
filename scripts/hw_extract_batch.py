"""Batch hardware spec extraction via LM Studio tool calls.

Reads hw_extract_input.json (device title + description),
calls lfm2.5-8b-a1b with tool_choice=required to extract structured
hardware fields, writes results to hw_extract_output.json.

Usage:
    python scripts/hw_extract_batch.py [--concurrency 2] [--max-tokens 2000]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
from pathlib import Path

import requests

LMSTUDIO_URL = "http://localhost:1234/v1/chat/completions"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

TOOL = {
    "type": "function",
    "function": {
        "name": "extract_hardware",
        "description": "Extract hardware specs from device description. Only include fields explicitly stated. Empty string if not mentioned.",
        "parameters": {
            "type": "object",
            "properties": {
                "hw_cpu": {"type": "string"},
                "hw_ram": {"type": "string"},
                "hw_storage": {"type": "string"},
                "hw_gpu": {"type": "string"},
                "hw_psu": {"type": "string"},
                "hw_motherboard": {"type": "string"},
                "hw_screen": {"type": "string"},
                "hw_network": {"type": "string"},
                "hw_other": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}


def extract_one(idx: int, device: dict) -> dict:
    """Extract hardware specs for one device via LM Studio tool call."""
    title = device["title"]
    desc = device["desc"]
    payload = {
        "model": "lfm2.5-8b-a1b",
        "messages": [{"role": "user", "content": f"Extract hardware specs from:\n{desc}"}],
        "tools": [TOOL],
        "tool_choice": "required",
        "temperature": 0.1,
        "max_tokens": 2000,
    }
    try:
        start = time.time()
        resp = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
        elapsed = time.time() - start
        if resp.status_code != 200:
            return {
                "idx": idx,
                "title": title,
                "fields": {},
                "elapsed": elapsed,
                "status": f"HTTP_{resp.status_code}",
                "reasoning_tokens": 0,
            }
        data = resp.json()
        msg = data["choices"][0]["message"]
        finish = data["choices"][0].get("finish_reason", "")
        rtok = data["usage"].get("completion_tokens_details", {}).get("reasoning_tokens", 0)
        extracted = {}
        if msg.get("tool_calls"):
            args = json.loads(msg["tool_calls"][0]["function"]["arguments"])
            extracted = {k: v for k, v in args.items() if v and str(v).strip()}
        return {
            "idx": idx,
            "title": title,
            "fields": extracted,
            "elapsed": round(elapsed, 1),
            "status": finish,
            "reasoning_tokens": rtok,
        }
    except Exception as e:
        return {
            "idx": idx,
            "title": title,
            "fields": {},
            "elapsed": 0,
            "status": "error",
            "reasoning_tokens": 0,
            "error": str(e),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch hardware extraction via LM Studio")
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel requests (LM Studio queues)")
    parser.add_argument("--max-tokens", type=int, default=2000, help="Max tokens per request")
    args = parser.parse_args()

    input_path = DATA_DIR / "hw_extract_input.json"
    output_path = DATA_DIR / "hw_extract_output.json"

    with open(input_path) as f:
        devices = json.load(f)

    print(f"Processing {len(devices)} devices, {args.concurrency} concurrent, {args.max_tokens} max_tokens")

    results = []
    start_all = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(extract_one, i, d) for i, d in enumerate(devices)]
        for future in concurrent.futures.as_completed(futures):
            r = future.result()
            results.append(r)
            if len(results) % 20 == 0:
                elapsed = time.time() - start_all
                ok = sum(1 for x in results if x["status"] == "tool_calls")
                has = sum(1 for x in results if len(x["fields"]) > 0)
                print(f"  {len(results)}/{len(devices)} ({elapsed:.0f}s) — {ok} ok, {has} with fields")

    total_wall = time.time() - start_all
    results.sort(key=lambda r: r["idx"])

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    # Stats
    ok = [r for r in results if r["status"] == "tool_calls"]
    has_fields = [r for r in ok if len(r["fields"]) > 0]
    empty = [r for r in ok if len(r["fields"]) == 0]
    length = [r for r in results if r["status"] == "length"]
    errors = [r for r in results if r["status"] not in ("tool_calls", "length")]

    print(f"\n{'=' * 60}")
    print(f"Wall time: {total_wall:.1f}s ({total_wall / 60:.1f} min)")
    print(f"Tool calls OK: {len(ok)}/{len(devices)}")
    print(f"Has fields: {len(has_fields)}")
    print(f"Empty (no hw): {len(empty)}")
    print(f"Token limit: {len(length)}")
    print(f"Errors: {len(errors)}")
    print(f"Throughput: {len(devices) / total_wall:.1f} dev/s")
    print(f"\nOutput: {output_path}")


if __name__ == "__main__":
    main()
