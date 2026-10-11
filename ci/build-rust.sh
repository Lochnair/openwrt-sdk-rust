#!/bin/sh
set -eu

: "${SCCACHE_REDIS_ENDPOINT:?Set the persistent sccache Redis endpoint}"
: "${SCCACHE_ENDPOINT:?Set the persistent sccache S3 endpoint}"
: "${AWS_ACCESS_KEY_ID:?Supply the S3 access key through a BuildKit secret}"
: "${AWS_SECRET_ACCESS_KEY:?Supply the S3 secret key through a BuildKit secret}"
: "${JOBS:=8}"
case "$JOBS" in ''|*[!0-9]*|0) echo 'JOBS must be a positive integer' >&2; exit 1 ;; esac

sccache --start-server
trap 'sccache-stats; sccache --stop-server' EXIT
sccache --zero-stats
# Bootstrap/Ninja does not inherit Make's job count. Bound both explicitly.
make package/rust/host/compile -j"$JOBS" RUST_BUILD_JOBS="$JOBS" || \
    make package/rust/host/compile -j1 RUST_BUILD_JOBS=1 V=sc
rm -rf build_dir/host/rustc-*/rustc-*/build dl/rustc-*.tar.*
