# openwrt-sdk-rust

For a reusable GitHub/Gitea Action that generates one upstream representative
target per package architecture and release, see
[the OpenWrt matrix Action](actions/openwrt-matrix/README.md). It is independent
of the SDK image-building workflow described below.

Builds the OpenWrt SDK with its Rust host toolchain installed. Gitea Actions
resolves `stable_version` and `oldstable_version` from OpenWrt's `.versions.json`.
The default `targets=all` selects the SDK tags actually available in GHCR for
each release, excluding architectures unsupported by OpenWrt's Rust package.
Release metadata supplies architectures, with release source definitions for
SDK-only targets omitted from `.targets.json`. Alternatively, supply a
comma-separated target list; unavailable or unsupported combinations are skipped
and reported, and an empty matrix fails. Images still use
`ghcr.io/openwrt/sdk:<target-with-dashes>-<version>` and
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
in fresh containers and requires writes to both tiers, Redis/S3 hits, and backfilling.
Each matrix job publishes independently after its own verification succeeds.
Its local SDK image is removed at the end, including after verification or push
failure and during non-publishing runs. Cleanup leaves the shared builder and
compilation caches intact.

Also set `SCCACHE_S3_ACCESS_KEY_ID` and `SCCACHE_S3_SECRET_ACCESS_KEY` from the
runner cache service in Gitea repository or organization secrets. These are needed
for non-publishing builds too; Docker Hub credentials are only used for publishing.

Run a non-publishing dispatch with
`targets=x86/64,mediatek/filogic,octeon/generic` for the six validation combinations, then an
approved publishing dispatch. Check the pulled image's Rust/Cargo installation
and downstream package build before disabling the old Jenkins job and deleting
`Jenkinsfile`. Do not retire Jenkins before this acceptance run. No Jenkins
plugins or credentials are used by the new workflow.

## Persistent compilation cache

The Gitea runner's `homelab-stacks/stacks/gitea-runner/compose.yaml` owns
Redis and the SSD-backed RustFS S3 service. This repository only prepares its
reusable BuildKit builder and consumes those services; jobs never deploy them.
Redis is a 1 GiB LRU hot cache with a 1.5 GiB memory limit. It is deliberately
non-persistent: S3 holds the persistent copies on a quota-limited 100 GiB Incus/ZFS
volume. Jobs, Redis restarts, and RustFS container recreation do not clear it.
The cache bucket expires objects after 30 days; the filesystem quota provides
an independent hard limit. See the runner stack's README for allocation and setup.

Each job runs its own pinned sccache 0.18.0 daemon with
`SCCACHE_MULTILEVEL_CHAIN=redis,s3` in the standard server-side mode.
Writes go to both tiers; Redis misses fall through to S3 and backfill Redis.
`SCCACHE_MULTILEVEL_WRITE_ERROR_POLICY=all` makes each cache write await both tiers.
The installed `sccache-stats` helper waits up to 30 seconds for outstanding writes
and backfills before printing statistics and stopping the job's daemon.
BuildKit's layer cache remains separate.

Both services join `gitea-runner-jobs`. SDK/package containers use
`redis://sccache-redis:6379` and `http://sccache-s3:9000`; BuildKit's host network
uses the loopback ports 6380 and 9000. There is no public ingress. S3 credentials
are supplied through required BuildKit secret mounts for compilation and runtime
environment variables for disposable verification/package containers. Credentials are not stored in the published SDK image. Runner endpoints are
build arguments rather than default runtime settings. The image
contains only public backend configuration: bucket `sccache`, region `us-east-1`,
path-style HTTP, and cache prefixes. Other environments can override these values.

Each OpenWrt release has a shared prefix across targets: `openwrt-rust:<version>:`
in Redis and `openwrt-rust/<version>/` in S3. Compiler identities, target,
flags, sources, and dependencies still determine cache keys, preventing
incompatible reuse. The old Redis service and its volume remain untouched during
this change; their entries are not copied into the new SSD cache.

A single persistent sccache daemon was tested across containers with client-side
compilation. The second container reported a cache error and recompiled: the
daemon's disk backend returns an absolute cache-file path that the client must
open, but the disposable container cannot see that filesystem. Version 0.18.0
also fixes TCP connections to loopback, and client-side raw reads bypass
multi-level backfilling and per-tier read statistics. Per-job daemons with remote
storage avoid filesystem sharing, socket proxies, and job-network changes.

RustFS and VersityGW were both tested with real Rust compilation, S3 fallback,
Redis backfilling, container restarts, and differing targets/toolchains. Both
passed. RustFS was selected following the owner's preference; the small storage
benchmark does not establish a full-build speedup. Deploy the runner cache and
initialize its bucket before running this workflow.

The shared BuildKit worker executes one build step at a time, even across
simultaneous dispatches. It is limited to eight CPUs and 20 GiB RAM with no
swap. Make and Rust bootstrap both receive eight jobs; the original `-j1 V=sc`
diagnostic retry also reduces bootstrap/Ninja to one job. Rust
build-directory/archive cleanup remains. BuildKit
layer storage has a 50 GiB GC budget. Matrix jobs also run one at a time, with
`fail-fast: false` so a failed target does not stop the remaining builds.

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
[sccache multi-level cache](https://github.com/mozilla/sccache/blob/v0.18.0/docs/MultiLevel.md),
[sccache client-side architecture](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Architecture.md),
[Rust cache limitations](https://github.com/mozilla/sccache/blob/v0.18.0/docs/Rust.md).

## Downstream OpenWrt packages

Use the same cache services and target/version image. Export `AWS_ACCESS_KEY_ID`
and `AWS_SECRET_ACCESS_KEY` from CI secrets before starting the container. For example, after
installing the `sqm-autorate-rust` feed/package in the SDK:

```sh
docker run --rm --network gitea-runner-jobs --cpus 8 \
  --memory 20g --memory-swap 20g \
  -e SCCACHE_REDIS_ENDPOINT=redis://sccache-redis:6379 \
  -e SCCACHE_ENDPOINT=http://sccache-s3:9000 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
  lochnair/openwrt-sdk-rust:x86-64-24.10.8 sh -ec '
    # Install/update the downstream feed here before defconfig.
    make defconfig
    grep -qx "CONFIG_RUST_SCCACHE=y" .config
    sccache --start-server
    trap "sccache-stats; sccache --stop-server" EXIT
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

For a local SDK build on the prepared runner, export `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` from the cache credentials in your environment first:

```sh
sh ci/setup-builder.sh
docker buildx build --builder openwrt-sdk-rust --load --progress plain \
  --network host --allow network.host \
  --build-arg SAFE_TARGET=x86-64 --build-arg VERSION=24.10.8 \
  --build-arg JOBS=8 \
  --build-arg SCCACHE_REDIS_ENDPOINT=redis://127.0.0.1:6380 \
  --build-arg SCCACHE_ENDPOINT=http://127.0.0.1:9000 \
  --secret id=aws-access-key-id,env=AWS_ACCESS_KEY_ID \
  --secret id=aws-secret-access-key,env=AWS_SECRET_ACCESS_KEY \
  -t lochnair/openwrt-sdk-rust:x86-64-24.10.8 .
```

See [validation results](ci/VALIDATION.md) for the checks performed and the
remaining cutover acceptance.
