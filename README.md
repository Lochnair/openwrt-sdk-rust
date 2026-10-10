# openwrt-sdk-rust

Builds the OpenWrt SDK with its Rust host toolchain installed. Gitea Actions
resolves `stable_version` and `oldstable_version` from OpenWrt's `.versions.json`.
The default targets remain `x86/64,mediatek/filogic,octeon/generic`; dispatch with
`targets=all` to use upstream `dump-target-info.pl`, or supply a comma-separated
list. Images still use `ghcr.io/openwrt/sdk:<target-with-dashes>-<version>` and
publish to `lochnair/openwrt-sdk-rust:<target-with-dashes>-<version>` on Docker Hub.
No `latest` tag or additional registry is introduced.

## Runner setup and cutover

The workflow uses `ubuntu-latest` on the existing TheBeast runner, currently
the only runner. Its job image and access to the persistent Docker daemon were
verified; no additional runner, label, capacity change, or restart is required.
`builtin:checkout` uses the existing Runner 5 checkout support.

Enable repository Actions and set `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
secrets with push permission to `lochnair/openwrt-sdk-rust`. Jenkins' credential
ID is not reusable in Gitea. The workflow is manually dispatched, matching the
parameterized Jenkins job; there are no PR, push, or scheduled publication
triggers. `publish=false` is the safe validation default. Dispatch
`publish=true` only after publication is approved. Login uses a temporary job
directory, and each successfully built and checked image is pushed under its
original tag. Before any push, `ci/verify-cache.sh` uses the installed SDK rustc
in two disposable containers and requires a Redis miss followed by a hit.

Run a non-publishing dispatch for the default six combinations, then an
approved publishing dispatch. Check the pulled image's Rust/Cargo installation
and downstream package build before disabling the old Jenkins job and deleting
`Jenkinsfile`. Do not retire Jenkins before this acceptance run. No Jenkins
plugins or credentials are used by the new workflow.

## Persistent compilation cache

`ci/setup-builder.sh` provisions one Redis service and one reusable BuildKit
builder on that daemon. Redis uses the named volume `openwrt-rust-sccache`, AOF
persistence (`appendfsync everysec`), a 6 GiB LRU cache, an 8 GiB memory limit,
and one CPU. Jobs and build containers can disappear without losing it. A
machine crash can lose up to the most recent second of cache writes; eviction
or lost entries cause recompilation. Do not run `docker compose down -v` or
prune this volume. BuildKit's layer cache is separate and may be garbage
collected without clearing sccache.

The cache joins the existing `gitea-runner-jobs` network and exposes port 6379
only on loopback. BuildKit uses host networking to reach it; package containers
use its Docker DNS name. This is a trusted-runner cache: do not attach untrusted
PR containers or public networks to it. No password or endpoint is baked into
published SDK images. Consumers outside this runner can use their own Redis
endpoint. Each OpenWrt version has its own key prefix, shared across targets;
sccache also
hashes Rust compiler version and shared libraries, compilation arguments
(including target and flags), source and dependency contents. Different Rust
versions/toolchains therefore miss rather than reuse incompatible objects.
Previously populated target-specific namespaces are not reused by the new
prefixes and will eventually be evicted by Redis.

The shared BuildKit worker executes one build step at a time, even across
simultaneous dispatches. It is limited to eight CPUs and 20 GiB RAM with no
swap. Make and Rust bootstrap both receive eight jobs; the original `-j1 V=sc`
diagnostic retry also reduces bootstrap/Ninja to one job. Rust
build-directory/archive cleanup remains. BuildKit
layer storage has a 50 GiB GC budget. Matrix jobs also run one at a time and
stop scheduling on a failure, as Jenkins' sequential stage loop did.

OpenWrt integration was inspected in the SDK's pinned packages feed:

- `rust-values.mk` **unexports `RUSTC_WRAPPER`**. Setting it in the workflow
  alone does not enable caching. `CONFIG_RUST_SCCACHE=y` makes OpenWrt inject
  `CARGO_INCREMENTAL=0`, `RUSTC_WRAPPER=sccache`, and `SCCACHE_DIR` into host
  and package Cargo invocations. The Dockerfile enables and checks that option.
- The base SDK has no sccache binary. We install the pinned 0.18.0 amd64-musl
  release and verify its SHA-256 before using it.
- Rust bootstrap wraps rustc itself. We also set `build.ccache=sccache` in
  its configure arguments so stage-zero compilation and LLVM use sccache,
  retaining `llvm.download-ci-llvm=false`. The inherited wrapper is forwarded
  by bootstrap through `RUSTC_WRAPPER_REAL` where applicable.
- sccache is a compiler cache, not a Cargo target-directory cache. Linked
  binaries, proc macros, some bootstrap work and incremental compilation are
  not cacheable. SDK bootstrap and downstream dependencies can share storage
  without necessarily sharing every compiled object. Every actual SDK build
  prints statistics on success or failure. BuildKit can reuse unchanged layers;
  a cached compilation layer does not run sccache or produce fresh statistics.

Sources: [OpenWrt Rust configuration](https://github.com/openwrt/packages/blob/openwrt-24.10/lang/rust/rust-values.mk),
[Rust bootstrap](https://github.com/rust-lang/rust/blob/1.90.0/src/bootstrap/src/core/build_steps/compile.rs),
[sccache Redis](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Redis.md),
[Rust cache limitations](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Rust.md).

## Downstream OpenWrt packages

Use the same persistent daemon and target/version image. For example, after
installing the `sqm-autorate-rust` feed/package in the SDK:

```sh
docker run --rm --network gitea-runner-jobs --cpus 8 \
  --memory 20g --memory-swap 20g \
  -e SCCACHE_REDIS_ENDPOINT=redis://openwrt-rust-cache:6379 \
  lochnair/openwrt-sdk-rust:x86-64-24.10.8 sh -ec '
    # Install/update the downstream feed here before defconfig.
    make defconfig
    grep -qx "CONFIG_RUST_SCCACHE=y" .config
    sccache --start-server
    trap "sccache --show-stats; sccache --stop-server" EXIT
    sccache --zero-stats
    make package/sqm-autorate-rust/compile -j8 V=sc
  '
```

Keep the image's cache prefix and enable `CONFIG_RUST_SCCACHE` after any
downstream configuration replacement. Cache statistics belong to the current
container's sccache server; `--zero-stats` does not erase cached objects.
`sqm-autorate-rust` currently uses cross-rs to publish static binaries, not an
OpenWrt SDK package workflow; that unrelated pipeline is unchanged. The package
Makefile in `openwrt-feeds` already uses OpenWrt's `rust-package.mk`, so an SDK
package job can use this cache without patching the package.

For a local SDK build on the prepared runner:

```sh
sh ci/setup-builder.sh
docker buildx build --builder openwrt-sdk-rust --load --progress plain \
  --network host --allow network.host \
  --build-arg SAFE_TARGET=x86-64 --build-arg VERSION=24.10.8 \
  --build-arg JOBS=8 \
  --build-arg SCCACHE_REDIS_ENDPOINT=redis://127.0.0.1:6379 \
  -t lochnair/openwrt-sdk-rust:x86-64-24.10.8 .
```

See [validation results](ci/VALIDATION.md) for the checks performed and the
remaining cutover acceptance.
