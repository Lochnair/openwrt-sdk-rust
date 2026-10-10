#!/bin/sh
set -eu
docker compose -p openwrt-rust -f ci/cache.compose.yml up -d --wait
if ! docker buildx inspect openwrt-sdk-rust >/dev/null 2>&1; then
    docker buildx create --name openwrt-sdk-rust --driver docker-container \
        --driver-opt network=host,memory=20g,memory-swap=20g,cpu-period=100000,cpu-quota=800000 \
        --buildkitd-config ci/buildkitd.toml \
        --buildkitd-flags '--allow-insecure-entitlement network.host' || \
        docker buildx inspect openwrt-sdk-rust
fi
docker buildx inspect openwrt-sdk-rust --bootstrap
