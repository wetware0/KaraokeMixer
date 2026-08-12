"""Dependency-free stand-in worker script for backend/app/workers/runner.py
tests. Runs under the *test* interpreter (sys.executable), never a real
worker venv - no torch/demucs/audio-separator import here or anywhere else
in this file."""
from __future__ import annotations

import json
import os
import sys
import time


def handle(args: dict) -> None:
    mode = args.get("mode", "success")

    if mode == "success":
        for i in range(args.get("progress_steps", 2)):
            print(json.dumps({"type": "progress", "message": f"step {i + 1}"}), flush=True)
        payload = {"echo": args.get("echo")}
        if args.get("include_pid"):
            payload["pid"] = os.getpid()
        print(json.dumps({"type": "result", "status": "completed", "payload": payload}), flush=True)
        return

    if mode == "fail_result":
        print(json.dumps({"type": "progress", "message": "about to fail"}), flush=True)
        print(json.dumps({"type": "result", "status": "failed", "error": "synthetic failure"}), flush=True)
        return

    if mode == "crash":
        print("about to crash", file=sys.stderr, flush=True)
        sys.exit(3)

    if mode == "sleep":
        remaining = float(args.get("duration_seconds", 5.0))
        print(json.dumps({"type": "progress", "message": "sleeping"}), flush=True)
        while remaining > 0:
            time.sleep(0.1)
            remaining -= 0.1
        print(json.dumps({"type": "result", "status": "completed", "payload": {}}), flush=True)
        return

    if mode == "result_then_sleep":
        # Emits its result immediately, then keeps the process alive for a
        # bit before exiting - used to prove that a cancel/timeout arriving
        # after the result line was already parsed does not override it.
        print(json.dumps({"type": "progress", "message": "about to finish"}), flush=True)
        print(
            json.dumps({"type": "result", "status": "completed", "payload": {"finished": True}}),
            flush=True,
        )
        time.sleep(float(args.get("post_result_sleep_seconds", 2.0)))
        return

    raise ValueError(f"unknown mode: {mode}")


def main() -> None:
    persistent = "--persistent" in sys.argv[1:]
    for line in sys.stdin:
        if not line.strip():
            continue
        handle(json.loads(line))
        if not persistent:
            break


if __name__ == "__main__":
    main()
