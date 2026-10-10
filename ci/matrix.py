#!/usr/bin/env python3
"""Resolve the same stable/oldstable x target matrix as Jenkins."""
import json
import os
import re
import subprocess
import tempfile
import urllib.request


def resolve(versions, targets):
    versions = [versions[k] for k in ("stable_version", "oldstable_version") if versions.get(k)]
    if not versions:
        raise ValueError("OpenWrt returned no stable or oldstable version")
    targets = [t.strip() for t in targets.split(",") if t.strip()]
    if not targets:
        raise ValueError("No targets selected")
    for target in targets:
        if not re.fullmatch(r"[a-z0-9_+-]+/[a-z0-9_+-]+", target):
            raise ValueError(f"Invalid target: {target!r}")
    for version in versions:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", version):
            raise ValueError(f"Invalid version: {version!r}")
    return {"include": [{"target": t.replace("/", "-"), "version": v}
                        for v in versions for t in dict.fromkeys(targets)]}


if __name__ == "__main__":
    with urllib.request.urlopen("https://downloads.openwrt.org/.versions.json", timeout=60) as response:
        versions = json.load(response)
    targets = os.environ.get("TARGETS", "x86/64,mediatek/filogic,octeon/generic")
    if targets.strip() == "all":
        with tempfile.TemporaryDirectory(prefix="openwrt-targets-") as source:
            subprocess.run(["git", "clone", "--depth", "1", "https://github.com/openwrt/openwrt.git", source], check=True)
            output = subprocess.check_output(["perl", "scripts/dump-target-info.pl", "targets"], cwd=source, text=True)
            targets = ",".join(line.split()[0] for line in output.splitlines() if line.strip())
    matrix = json.dumps(resolve(versions, targets), separators=(",", ":"))
    print(matrix)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"matrix={matrix}\n")
