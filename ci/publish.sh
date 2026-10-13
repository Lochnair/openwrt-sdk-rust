#!/bin/sh
set -eu

mode=$1
tag=$2
case "$mode" in build|sync) ;; *) echo 'Expected build or sync' >&2; exit 1 ;; esac
: "${DOCKERHUB_USERNAME:?Set the Docker Hub username}"
: "${DOCKERHUB_TOKEN:?Set the Docker Hub token}"
: "${GHCR_USERNAME:?Set the GitHub username}"
: "${GHCR_TOKEN:?Set a GitHub token with package write access}"

DOCKER_CONFIG=$(mktemp -d)
export DOCKER_CONFIG
REGISTRY_AUTH_FILE="$DOCKER_CONFIG/config.json"
export REGISTRY_AUTH_FILE
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
printf '%s' "$DOCKERHUB_TOKEN" | docker login --username "$DOCKERHUB_USERNAME" --password-stdin
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
if [ "$mode" = build ]; then
    docker push "lochnair/openwrt-sdk-rust:$tag"
fi
python3 ci/registry.py "$tag" "${3:-}"
