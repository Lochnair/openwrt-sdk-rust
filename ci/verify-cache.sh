#!/bin/sh
# Compile in two disposable containers: the second must read the first's cache.
set -eu
image=${1:?Usage: verify-cache.sh IMAGE}
nonce=$(mktemp -d)
trap 'rmdir "$nonce"' EXIT
prefix="validation:${image##*:}:$(basename "$nonce"):"
for expected in miss hit; do
    docker run --rm -i --network gitea-runner-jobs --cpus 2 \
        --memory 2g --memory-swap 2g \
        -e SCCACHE_REDIS_ENDPOINT=redis://openwrt-rust-cache:6379 \
        -e SCCACHE_REDIS_KEY_PREFIX="$prefix" -e EXPECTED="$expected" \
        "$image" sh -es <<'PROBE'
rustc=$(find /builder/staging_dir -type f -path '*/host/bin/rustc')
test -x "$rustc"
mkdir /tmp/sccache-probe
cd /tmp/sccache-probe
printf 'pub fn probe(n: u64) -> u64 { n.wrapping_mul(17) }\n' > probe.rs
sccache --start-server
trap 'sccache --stop-server' EXIT
sccache --zero-stats
sccache "$rustc" --crate-name ci_sccache_probe \
    --crate-type rlib --emit link,dep-info --out-dir . probe.rs
sccache --show-stats
sccache --show-stats --stats-format json > stats.json
python3 - <<'PY'
import json
import os

data = json.load(open("stats.json"))
assert data["cache_location"].startswith("redis,"), data
stats = data["stats"]
field = "cache_hits" if os.environ["EXPECTED"] == "hit" else "cache_misses"
assert sum(stats[field]["counts"].values()) == 1, data
assert stats["cache_read_errors"] == stats["cache_write_errors"] == 0, data
print("Verified persistent cache", os.environ["EXPECTED"])
PY
PROBE
done
