#!/usr/bin/env python3
"""Wait for this job's cache writes/backfills, then print complete statistics."""
import json
import subprocess
import time

end = time.monotonic() + 30
while True:
    data = json.loads(subprocess.check_output(
        ["sccache", "--show-stats", "--stats-format", "json"],
        timeout=max(0.1, end - time.monotonic()),
    ))
    stats = data["stats"]
    misses = sum(stats["cache_misses"]["counts"].values())
    levels = stats.get("multi_level") or []
    writes_done = stats["cache_writes"] + stats["cache_write_errors"] >= misses
    backfills_done = sum(x["backfills_to"] for x in levels) >= sum(
        x["backfills_from"] for x in levels
    )
    if writes_done and backfills_done:
        break
    if time.monotonic() >= end:
        subprocess.run(["sccache", "--show-stats"], check=True, timeout=5)
        raise SystemExit("Timed out waiting for sccache writes/backfills")
    time.sleep(0.1)
subprocess.run(["sccache", "--show-stats"], check=True, timeout=5)
