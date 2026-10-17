#!/usr/bin/env python3
"""Generate matrices using each release's upstream architecture grouping only."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request


VERSIONS_URL = "https://downloads.openwrt.org/.versions.json"
UPSTREAM = "https://github.com/openwrt/openwrt.git"
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-rc\d+)?")
ARCH = re.compile(r"[a-zA-Z0-9_+.-]+")
TARGET = re.compile(r"[a-zA-Z0-9_+.-]+/[a-zA-Z0-9_+.-]+")


def items(value):
    parts = [part.strip() for part in value.split(",")]
    if not all(parts):
        raise ValueError("Expected a nonempty comma-separated list")
    return list(dict.fromkeys(parts))


def fetch_versions():
    with urllib.request.urlopen(VERSIONS_URL, timeout=60) as response:
        metadata = json.load(response)
    if not isinstance(metadata, dict):
        raise ValueError("Invalid OpenWrt .versions.json: expected an object")
    return metadata


def resolve_versions(requested, metadata):
    versions = []
    for value in items(requested):
        version = metadata.get(f"{value}_version") if value in ("stable", "oldstable") else value
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError(f"Cannot resolve OpenWrt release: {value!r}")
        if version not in versions:
            versions.append(version)
    return versions


def parse_architectures(output):
    architectures = {}
    seen_targets = set()
    for number, line in enumerate(output.splitlines(), 1):
        if not line.strip():
            continue
        arch, *targets = line.split()
        if (not ARCH.fullmatch(arch) or not targets
                or any(not TARGET.fullmatch(target) for target in targets)
                or arch in architectures or len(set(targets)) != len(targets)
                or seen_targets.intersection(targets)):
            raise ValueError(f"Invalid upstream architectures output at line {number}: {line!r}")
        architectures[arch] = targets
        seen_targets.update(targets)
    if not architectures:
        raise ValueError("Upstream returned no package architectures")
    return architectures


def selection(value, pattern):
    selected = items(value)
    if selected == ["all"]:
        return None
    if "all" in selected or any(not pattern.fullmatch(item) for item in selected):
        raise ValueError(f"Invalid filter: {value!r}; use all or a comma-separated list")
    return set(selected)


def select_rows(version, architectures, package_arches, targets):
    for label, requested, available in (
        ("package architectures", package_arches, set(architectures)),
        ("firmware targets", targets, {t for group in architectures.values() for t in group}),
    ):
        missing = requested - available if requested is not None else set()
        if missing:
            raise ValueError(f"OpenWrt {version}: unavailable {label}: {', '.join(sorted(missing))}")
    rows = [
        {"version": version, "package_arch": arch, "target": architectures[arch][0]}
        for arch in sorted(architectures)
        if (package_arches is None or arch in package_arches)
        and (targets is None or targets.intersection(architectures[arch]))
    ]
    if not rows:
        raise ValueError(f"OpenWrt {version}: filters produced an empty matrix")
    return rows


def run(command, cwd=None):
    try:
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Command failed: {command!r}\n{error.stderr}") from error


def release_architectures(version, directory):
    checkout = Path(directory) / version
    run(["git", "clone", "--quiet", "--depth", "1", "--single-branch",
         "--branch", f"v{version}", UPSTREAM, str(checkout)])
    # --branch can name a branch too; insist on the exact release tag.
    run(["git", "checkout", "--quiet", "--detach", f"refs/tags/v{version}"], cwd=checkout)
    (checkout / "tmp").mkdir(exist_ok=True)
    result = run(["perl", "scripts/dump-target-info.pl", "architectures"], cwd=checkout)
    # The upstream Perl script does not propagate all child make failures.
    # DUMP mode has a deliberately incomplete CPU_CFLAGS table and emits these
    # warnings on clean release trees. They do not affect architecture metadata.
    diagnostics = [line for line in result.stderr.splitlines() if line.strip() and not re.fullmatch(
        r'.*/include/target\.mk:\d+: CPU_TYPE "[a-zA-Z0-9_.+-]+" doesn\'t correspond to a known type', line)]
    if diagnostics:
        raise ValueError(f"OpenWrt {version}: upstream metadata diagnostics:\n{result.stderr}")
    return parse_architectures(result.stdout)


def generate(versions, package_architectures, targets):
    package_arches = selection(package_architectures, ARCH)
    firmware_targets = selection(targets, TARGET)
    releases = resolve_versions(versions, fetch_versions())
    rows = []
    with tempfile.TemporaryDirectory(prefix="openwrt-matrix-") as directory:
        for version in releases:
            try:
                available = release_architectures(version, directory)
                rows.extend(select_rows(version, available, package_arches, firmware_targets))
            except ValueError as error:
                raise ValueError(f"OpenWrt {version}: {error}") from error
    if not rows:
        raise ValueError("Resulting matrix is empty")
    return {"include": rows}


def main():
    try:
        matrix = generate(os.environ.get("MATRIX_VERSIONS", "stable,oldstable"),
                          os.environ.get("MATRIX_ARCHITECTURES", "all"),
                          os.environ.get("MATRIX_TARGETS", "all"))
        encoded = json.dumps(matrix, separators=(",", ":"))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"matrix={encoded}\n")
        print(encoded)
    except (ValueError, OSError) as error:
        print(f"Matrix generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
