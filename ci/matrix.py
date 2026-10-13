#!/usr/bin/env python3
"""Resolve Rust-supported SDK tags for each stable/oldstable release."""
import json
import hashlib
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

from registry import BASE_LABEL, RECIPE_LABEL, Registry, dockerhub, ghcr


# OpenWrt packages/lang/rust/rust-values.mk: RUST_ARCH_DEPENDS.
RUST_ARCHES = {
    "aarch64", "arm", "i386", "loongarch64", "mips", "mips64",
    "mips64el", "mipsel", "powerpc", "powerpc64", "riscv64", "x86_64",
}


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode()


def sdk_tags():
    token = json.loads(fetch("https://ghcr.io/token?service=ghcr.io&scope=repository:openwrt/sdk:pull"))["token"]
    url = "https://ghcr.io/v2/openwrt/sdk/tags/list?n=1000"
    tags = set()
    while url:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            tags.update(json.load(response)["tags"])
            link = response.headers.get("Link", "")
        next_page = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = urllib.parse.urljoin(url, next_page[1]) if next_page else None
    return tags


def release_targets(version, tags):
    base = f"https://downloads.openwrt.org/releases/{version}"
    # Directory names distinguish target/subtarget tags from architecture aliases.
    boards = re.findall(r'href="([a-z0-9_+-]+)/"', fetch(f"{base}/targets/"))
    suffix = f"-{version}"
    targets = sorted({f"{board}/{tag[len(board) + 1:-len(suffix)]}"
                      for board in boards for tag in tags
                      if tag.startswith(f"{board}-") and tag.endswith(suffix)})
    if not targets:
        raise ValueError(f"No SDK images available for OpenWrt {version}")
    architectures = json.loads(fetch(f"{base}/.targets.json"))
    result = {}
    for target in targets:
        if not re.fullmatch(r"[a-z0-9_+-]+/[a-z0-9_+-]+", target):
            raise ValueError(f"Invalid SDK target: {target!r}")
        if target in architectures:
            arch = architectures[target]
            arch = arch if arch in RUST_ARCHES else arch.split("_", 1)[0]
        else:
            # SDK-only targets (e.g. Malta) have no profiles in .targets.json.
            board, subtarget = target.split("/")
            source = f"https://raw.githubusercontent.com/openwrt/openwrt/v{version}/target/linux"
            definition = fetch(f"{source}/{board}/{subtarget}/target.mk")
            match = re.search(r"^ARCH\s*:?=\s*([a-z0-9_]+)\s*$", definition, re.MULTILINE)
            if not match:
                definition = fetch(f"{source}/{board}/Makefile")
                match = re.search(r"^ARCH\s*:?=\s*([a-z0-9_]+)\s*$", definition, re.MULTILINE)
            if not match:
                raise ValueError(f"Cannot determine architecture for {target} on {version}")
            arch = match[1]
        result[target] = arch
    return result


def resolve(versions, targets, tags):
    versions = [versions[k] for k in ("stable_version", "oldstable_version") if versions.get(k)]
    if not versions:
        raise ValueError("OpenWrt returned no stable or oldstable version")
    targets = [t.strip() for t in targets.split(",") if t.strip()]
    if not targets:
        raise ValueError("No targets selected")
    if "all" in targets and targets != ["all"]:
        raise ValueError("Select all or a comma-separated target list")
    for target in targets:
        if target != "all" and not re.fullmatch(r"[a-z0-9_+-]+/[a-z0-9_+-]+", target):
            raise ValueError(f"Invalid target: {target!r}")
    for version in versions:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", version):
            raise ValueError(f"Invalid version: {version!r}")
    matrix = []
    for version in dict.fromkeys(versions):
        available = release_targets(version, tags)
        selected = available if targets == ["all"] else dict.fromkeys(targets)
        supported = [t for t in selected if available.get(t) in RUST_ARCHES]
        skipped = [t for t in selected if t not in supported]
        print(f"OpenWrt {version}: {len(supported)} targets selected; "
              f"{len(available)} SDK images available", file=sys.stderr)
        if skipped:
            print(f"Skipped unavailable or Rust-unsupported targets: {', '.join(skipped)}", file=sys.stderr)
        matrix.extend({"target": t.replace("/", "-"), "version": version} for t in supported)
    if not matrix:
        raise ValueError("No available Rust-supported SDK targets selected")
    return {"include": matrix}


def recipe_fingerprint():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent.parent
    for name in ("Dockerfile", "ci/build-rust.sh", "ci/sccache-stats.py"):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def plan(candidates, force=False, upstream=None, source=None, destination=None):
    upstream = upstream or Registry("ghcr.io", "openwrt/sdk")
    source = source or dockerhub()
    destination = destination or ghcr()
    recipe = recipe_fingerprint()
    builds, syncs = [], []
    for entry in candidates["include"]:
        tag = f'{entry["target"]}-{entry["version"]}'
        base_digest = upstream.digest(tag)
        hub_digest = source.digest(tag, missing=True)
        labels = source.labels(hub_digest) if hub_digest and not force else {}
        # Existing unlabelled Docker Hub images remain authoritative. Only
        # metadata recorded by this pipeline can prove an image is outdated.
        outdated = (labels.get(BASE_LABEL, base_digest) != base_digest
                    or labels.get(RECIPE_LABEL, recipe) != recipe)
        if force or hub_digest is None or outdated:
            builds.append({**entry, "base_digest": base_digest, "recipe": recipe})
        elif destination.digest(tag, missing=True) != hub_digest:
            syncs.append({**entry, "hub_digest": hub_digest})
    print(f"Planned {len(builds)} builds and {len(syncs)} registry synchronizations", file=sys.stderr)
    return {"build_matrix": {"include": builds}, "sync_matrix": {"include": syncs}}


if __name__ == "__main__":
    versions = json.loads(fetch("https://downloads.openwrt.org/.versions.json"))
    targets = os.environ.get("TARGETS", "all")
    force = os.environ.get("FORCE_REBUILD", "false").lower()
    if force not in ("true", "false"):
        raise ValueError("FORCE_REBUILD must be true or false")
    matrices = plan(resolve(versions, targets, sdk_tags()), force=force == "true")
    print(json.dumps(matrices, separators=(",", ":")))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, matrix in matrices.items():
            output.write(f'{name}={json.dumps(matrix, separators=(",", ":"))}\n')
        output.write(f'has_builds={str(bool(matrices["build_matrix"]["include"])).lower()}\n')
        output.write(f'has_syncs={str(bool(matrices["sync_matrix"]["include"])).lower()}\n')
