#!/bin/sh
# Verify writes, Redis/S3 hits, and Redis backfilling in fresh containers.
set -eu
image=${1:?Usage: verify-cache.sh IMAGE}
: "${SCCACHE_REDIS_ENDPOINT:=redis://sccache-redis:6379}"
: "${SCCACHE_ENDPOINT:=http://sccache-s3:9000}"
: "${AWS_ACCESS_KEY_ID:?Set the S3 access key}"
: "${AWS_SECRET_ACCESS_KEY:?Set the S3 secret key}"
stats_script=$(cat ci/sccache-stats.py)
nonce=$(mktemp -d)
trap 'rmdir "$nonce"' EXIT
prefix="validation/${image##*:}/$(basename "$nonce")/"
for expected in miss hit s3-hit backfill backfill-hit; do
    redis_prefix=$prefix
    case "$expected" in backfill*) redis_prefix="${prefix}backfill/" ;; esac
    chain=redis,s3
    if [ "$expected" = s3-hit ]; then chain=s3; fi
    docker run --rm -i --network gitea-runner-jobs --cpus 2 \
        --memory 2g --memory-swap 2g \
        -e SCCACHE_REDIS_ENDPOINT="$SCCACHE_REDIS_ENDPOINT" \
        -e SCCACHE_ENDPOINT="$SCCACHE_ENDPOINT" \
        -e SCCACHE_REDIS_KEY_PREFIX="$redis_prefix" \
        -e SCCACHE_S3_KEY_PREFIX="$prefix" \
        -e SCCACHE_BUCKET=sccache -e SCCACHE_REGION=us-east-1 \
        -e SCCACHE_S3_USE_SSL=false \
        -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
        -e SCCACHE_MULTILEVEL_CHAIN="$chain" \
        -e SCCACHE_MULTILEVEL_WRITE_ERROR_POLICY=all \
        -e SCCACHE_CLIENT_SIDE=0 \
        -e SCCACHE_STATS_SCRIPT="$stats_script" \
        -e EXPECTED="$expected" "$image" sh -es <<'PROBE'
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
python3 -c "$SCCACHE_STATS_SCRIPT"
sccache --show-stats --stats-format json > stats.json
python3 - <<'PY'
import json
import os

data = json.load(open("stats.json"))
assert data["cache_location"].startswith("Multi-level"), data
stats = data["stats"]
expected = os.environ["EXPECTED"]
field = "cache_misses" if expected == "miss" else "cache_hits"
assert sum(stats[field]["counts"].values()) == 1, data
assert stats["cache_read_errors"] == stats["cache_write_errors"] == 0, data
assert sum(stats["cache_errors"]["counts"].values()) == 0, data
levels = stats["multi_level"]
assert all(level["write_failures"] == 0 for level in levels), data
if expected == "miss":
    assert len(levels) == 2 and all(level["writes"] == 1 for level in levels), data
elif expected == "backfill":
    assert levels[1]["hits"] == levels[0]["backfills_to"] == 1, data
else:
    assert levels[0]["hits"] == 1, data
print("Verified persistent cache", expected)
PY
PROBE
done
