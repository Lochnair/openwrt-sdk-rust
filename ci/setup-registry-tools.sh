#!/bin/sh
set -eu
if ! command -v skopeo >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
        apt-get update
        apt-get install -y --no-install-recommends skopeo
    else
        sudo apt-get update
        sudo apt-get install -y --no-install-recommends skopeo
    fi
fi
skopeo --version
