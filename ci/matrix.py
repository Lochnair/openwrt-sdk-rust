#!/usr/bin/env python3
"""Resolve Rust-supported SDK tags for each stable/oldstable release."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request


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


if __name__ == "__main__":
    versions = json.loads(fetch("https://downloads.openwrt.org/.versions.json"))
    targets = os.environ.get("TARGETS", "all")
    matrix = json.dumps(resolve(versions, targets, sdk_tags()), separators=(",", ":"))
    print(matrix)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"matrix={matrix}\n")
